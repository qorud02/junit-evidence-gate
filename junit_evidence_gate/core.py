"""Bounded JUnit parsing, record counting, and explicit acceptance policy."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat


@dataclass(frozen=True)
class Policy:
    min_executed: int = 1
    max_skipped: int | None = None
    max_skip_ratio: float | None = None
    max_bytes: int = 5 * 1024 * 1024
    max_nodes: int = 100_000
    max_depth: int = 100

    def __post_init__(self) -> None:
        for key in ("min_executed", "max_skipped"):
            value = getattr(self, key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{key} must be a nonnegative integer")
        if self.max_skip_ratio is not None:
            if isinstance(self.max_skip_ratio, bool) or not isinstance(self.max_skip_ratio, (int, float)) or not 0 <= self.max_skip_ratio <= 1:
                raise ValueError("max_skip_ratio must be a number between 0 and 1")
        for key in ("max_bytes", "max_nodes", "max_depth"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a positive integer")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    source: str = ""


@dataclass(frozen=True)
class Case:
    source: str
    suite: tuple[str, ...]
    classname: str
    name: str
    status: str
    has_failure: bool = False
    has_error: bool = False

    @property
    def identity(self) -> tuple[tuple[str, ...], str, str]:
        return self.suite, self.classname, self.name


@dataclass
class Report:
    policy: Policy
    files: list[str] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def unique_cases(self) -> list[Case]:
        # Failed duplicate reports must never inflate the minimum-execution gate.
        rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
        by_identity: dict[tuple[tuple[str, ...], str, str], Case] = {}
        for case in self.cases:
            previous = by_identity.get(case.identity)
            if previous is None or rank[case.status] > rank[previous.status]:
                by_identity[case.identity] = case
        return list(by_identity.values())

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(case.status for case in self.unique_cases)
        return {
            "records": len(self.cases),
            "unique": len(self.unique_cases),
            "executed": counts["passed"] + counts["failed"] + counts["error"],
            "passed": counts["passed"],
            "failed": counts["failed"],
            "errors": counts["error"],
            "skipped": counts["skipped"],
        }

    @property
    def exit_code(self) -> int:
        if any(issue.code.startswith("input.") for issue in self.issues):
            return 2
        return 1 if self.issues else 0

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "accepted": self.exit_code == 0,
            "exit_code": self.exit_code,
            "counts": self.counts,
            "policy": asdict(self.policy),
            "files": self.files,
            "issues": [asdict(issue) for issue in self.issues],
            "cases": [asdict(case) for case in self.cases],
        }


class UnsafeXML(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(data: bytes, policy: Policy) -> ET.Element:
    """Use Expat declaration callbacks so UTF-16/32 cannot bypass DTD checks."""
    parser = expat.ParserCreate(namespace_separator="}")
    builder = ET.TreeBuilder()
    depth = nodes = 0

    def reject(*_args: object) -> None:
        raise UnsafeXML("DTD and entity declarations are disabled")

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal depth, nodes
        depth += 1
        nodes += 1
        if depth > policy.max_depth:
            raise UnsafeXML("XML depth exceeds the configured limit")
        if nodes > policy.max_nodes:
            raise UnsafeXML("XML node count exceeds the configured limit")
        builder.start(name, attrs)

    def end(name: str) -> None:
        nonlocal depth
        builder.end(name)
        depth -= 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.Parse(data, True)
    return builder.close()


def _read_file(path: Path, policy: Policy) -> ET.Element:
    # Read limit+1 rather than read_bytes(): an oversized file stays bounded.
    with path.open("rb") as stream:
        data = stream.read(policy.max_bytes + 1)
    if len(data) > policy.max_bytes:
        raise UnsafeXML("XML file size exceeds the configured limit")
    return _parse(data, policy)


def _case(node: ET.Element, source: str, suite: tuple[str, ...], issues: list[Issue]) -> Case:
    name = node.get("name", "")
    if not name.strip():
        issues.append(Issue("evidence.missing_name", "A testcase has no nonblank name", source))
    tags = {_local(child.tag) for child in node}
    unknown = tags - {"error", "failure", "skipped", "properties", "system-out", "system-err"}
    if unknown:
        issues.append(Issue("evidence.unsupported_result", f"Unsupported testcase elements: {', '.join(sorted(unknown))}", source))
    has_error = "error" in tags
    has_failure = "failure" in tags
    skipped = "skipped" in tags
    declared = node.get("status", "").strip().lower()
    states = {
        "": "passed", "passed": "passed", "pass": "passed", "run": "passed",
        "completed": "passed", "failed": "failed", "failure": "failed",
        "error": "error", "skipped": "skipped", "skip": "skipped",
        "notrun": "skipped", "disabled": "skipped",
    }
    if declared not in states:
        issues.append(Issue("evidence.unknown_status", f"Unknown testcase status: {declared}", source))
    status = "error" if has_error else "failed" if has_failure else "skipped" if skipped else states.get(declared, "skipped")
    if skipped and (has_error or has_failure):
        issues.append(Issue("evidence.conflicting_status", f"Testcase {name} records both skip and failure/error", source))
    if declared and declared in states and tags & {"error", "failure", "skipped"}:
        if states[declared] != status and declared not in {"run", "completed"}:
            issues.append(Issue("evidence.conflicting_status", f"Testcase {name} status conflicts with its result element", source))
    return Case(source, suite, node.get("classname", ""), name, status,
                has_failure or status == "failed", has_error or status == "error")


def _collect(root: ET.Element, source: str, issues: list[Issue]) -> list[Case]:
    if _local(root.tag) not in {"testsuite", "testsuites"}:
        issues.append(Issue("input.root", "Expected testsuite or testsuites as the XML root", source))
        return []
    cases: list[Case] = []

    def visit(node: ET.Element, suite: tuple[str, ...], parent_tag: str = "") -> list[Case]:
        tag = _local(node.tag)
        if tag == "testcase":
            if parent_tag != "testsuite":
                issues.append(Issue("evidence.structure", "A testcase must be a direct child of a testsuite", source))
            if any(_local(child.tag) in {"testcase", "testsuite", "testsuites"} for child in node.iter() if child is not node):
                issues.append(Issue("evidence.structure", "A testcase contains a nested test container", source))
            if any(
                _local(desc.tag) in {"error", "failure", "skipped"}
                for child in node for desc in child.iter() if desc is not child
            ):
                issues.append(Issue("evidence.structure", "A testcase result must be a direct child of its testcase", source))
            case = _case(node, source, suite, issues)
            cases.append(case)
            return [case]
        if tag not in {"testsuite", "testsuites"}:
            if any(_local(desc.tag) in {"testcase", "testsuite", "testsuites", "error", "failure", "skipped"} for desc in node.iter()):
                issues.append(Issue("evidence.structure", "A test container or result appears outside its supported parent", source))
            return []
        path = suite + (node.get("name", "(unnamed)"),) if tag == "testsuite" else suite
        descendants: list[Case] = []
        for child in node:
            descendants.extend(visit(child, path, tag))
        observed = {
            "tests": len(descendants),
            "failures": sum(case.has_failure for case in descendants),
            "errors": sum(case.has_error for case in descendants),
            "skipped": sum(case.status == "skipped" for case in descendants),
        }
        for attr, actual in observed.items():
            raw = node.get(attr)
            if raw is None:
                continue
            if not re.fullmatch(r"[0-9]+", raw) or len(raw) > 12:
                issues.append(Issue("evidence.invalid_count", f"{tag} {attr} is not a supported nonnegative integer", source))
            elif int(raw) != actual:
                issues.append(Issue("evidence.count_mismatch", f"{tag} {node.get('name', '(unnamed)')} declares {attr}={raw}; observed {actual}", source))
        return descendants

    visit(root, ())
    return cases


def inspect_reports(paths: list[str | Path], policy: Policy | None = None) -> Report:
    report = Report(policy or Policy())
    seen_files: set[Path] = set()
    if not paths:
        report.issues.append(Issue("input.missing", "No report files supplied"))
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except (OSError, ValueError, RuntimeError):
            report.issues.append(Issue("input.unreadable", "Report file cannot be read", str(path)))
            continue
        if resolved in seen_files:
            continue
        seen_files.add(resolved)
        report.files.append(str(path))
        try:
            root = _read_file(path, report.policy)
        except UnsafeXML as exc:
            report.issues.append(Issue("input.unsafe_xml", str(exc), str(path)))
            continue
        except expat.ExpatError:
            report.issues.append(Issue("input.invalid_xml", "Malformed or unsupported XML encoding", str(path)))
            continue
        except (OSError, ValueError):
            report.issues.append(Issue("input.unreadable", "Report file cannot be read", str(path)))
            continue
        report.cases.extend(_collect(root, str(path), report.issues))
    identities: set[tuple[tuple[str, ...], str, str]] = set()
    for case in report.cases:
        if case.identity in identities:
            report.issues.append(Issue("evidence.duplicate", f"Repeated testcase identity: {case.classname}::{case.name} in {' / '.join(case.suite)}", case.source))
        identities.add(case.identity)
    counts = report.counts
    if not counts["executed"]:
        report.issues.append(Issue("policy.zero_executed", "No non-skipped testcase evidence was recorded"))
    elif counts["executed"] < report.policy.min_executed:
        report.issues.append(Issue("policy.min_executed", f"Executed {counts['executed']}; minimum is {report.policy.min_executed}"))
    if counts["failed"] or counts["errors"]:
        report.issues.append(Issue("policy.failed", f"Recorded {counts['failed']} failed and {counts['errors']} error testcases"))
    if report.policy.max_skipped is not None and counts["skipped"] > report.policy.max_skipped:
        report.issues.append(Issue("policy.max_skipped", f"Skipped {counts['skipped']}; maximum is {report.policy.max_skipped}"))
    ratio = counts["skipped"] / counts["unique"] if counts["unique"] else 0.0
    if report.policy.max_skip_ratio is not None and ratio > report.policy.max_skip_ratio:
        report.issues.append(Issue("policy.skip_ratio", f"Skip ratio {ratio:.6f}; maximum is {report.policy.max_skip_ratio}"))
    return report
