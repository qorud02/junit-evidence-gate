"""CLI with JSON, literal-safe Markdown, and GitHub Actions annotations."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import string
import sys

from . import __version__
from .core import Issue, Policy, Report, inspect_reports
from .github import render_github


def markdown_literal(value: str) -> str:
    """Escape punctuation, controls, and CR/LF without inventing Markdown syntax."""
    parts: list[str] = []
    for char in value.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            parts.append("<br>")
        elif ord(char) < 32 or ord(char) == 127:
            parts.append(f"&#92;u{ord(char):04x}")
        elif char in string.punctuation:
            parts.append(f"&#{ord(char)};")
        else:
            parts.append(char)
    return "".join(parts)


def render_markdown(report: Report) -> str:
    verdict = "PASS" if report.exit_code == 0 else "REJECT"
    lines = [f"# JUnit evidence: {verdict}", "", "| Measure | Count |", "| --- | ---: |"]
    lines.extend(f"| {name} | {count} |" for name, count in report.counts.items())
    if report.issues:
        lines.extend(["", "| Check | Detail | Source |", "| --- | --- | --- |"])
        lines.extend(f"| {markdown_literal(issue.code)} | {markdown_literal(issue.message)} | {markdown_literal(issue.source)} |" for issue in report.issues)
    lines.extend(["", "| Suite | Class | Testcase | Status |", "| --- | --- | --- | --- |"])
    for case in report.cases:
        cells = (" / ".join(case.suite), case.classname, case.name, case.status)
        lines.append("| " + " | ".join(markdown_literal(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject empty, contradictory, duplicate, or failing JUnit evidence.")
    parser.add_argument("reports", nargs="+", help="XML paths or quoted glob patterns; each pattern must match")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--min-executed", type=int, default=1)
    parser.add_argument("--max-skipped", type=int)
    parser.add_argument("--max-skip-ratio", type=float)
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--format", choices=("json", "markdown", "github"), default="json")
    parser.add_argument("--output", type=Path, help="Write the same summary to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        policy = Policy(args.min_executed, args.max_skipped, args.max_skip_ratio, args.max_bytes)
    except ValueError as exc:
        parser.error(str(exc))
    paths: list[str] = []
    unmatched: list[str] = []
    for pattern in args.reports:
        if not glob.has_magic(pattern) and Path(pattern).exists():
            paths.append(pattern)
            continue
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            paths.extend(matches)
        else:
            unmatched.append(pattern)
    report = inspect_reports(paths, policy)
    report.issues.extend(Issue("input.unmatched", "Path or glob pattern matched no files", pattern) for pattern in unmatched)
    if args.format == "github":
        content = render_github(report)
    elif args.format == "markdown":
        content = render_markdown(report)
    else:
        content = json.dumps(report.to_dict(), ensure_ascii=True, indent=2) + "\n"
    if args.output:
        try:
            resolved_output = args.output.resolve()
            if any(resolved_output == Path(path).resolve() or (args.output.exists() and Path(path).exists() and args.output.samefile(path)) for path in report.files):
                print("Output path must differ from every input report", file=sys.stderr)
                return 2
            args.output.write_text(content, encoding="utf-8")
        except (OSError, ValueError):
            print("Summary output cannot be written", file=sys.stderr)
            return 2
    else:
        try:
            sys.stdout.write(content)
        except BrokenPipeError:
            return 2
    return report.exit_code
