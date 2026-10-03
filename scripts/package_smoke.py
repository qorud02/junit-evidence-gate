"""Exercise installed wheel or non-root container before publishing."""
import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--container", help="Exact local image tag to test")
mode.add_argument("--python", help="Python from an isolated wheel environment")
parser.add_argument("--console", help="Installed console command in that environment")
args = parser.parse_args()
if args.python and not args.console:
    parser.error("--python requires --console")
root = Path.cwd().resolve()
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
clean_env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}}
checks = []

with tempfile.TemporaryDirectory(prefix="junit-package-smoke-") as directory:
    temp = Path(directory).resolve()
    assert temp.parent == Path(tempfile.gettempdir()).resolve()
    assert temp.name.startswith("junit-package-smoke-")
    temp.chmod(0o755)
    injection_name = "github-👋,%0A-case.xml"
    injection_case = "한글 👋\r\n::error::forged%0A"
    injection_suite = "suite 👋\n::notice::suite"
    payloads = {
        "malformed.xml": b"<testsuite>",
        "dtd-utf8.xml": b'<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="&x;"/></testsuite>',
        "dtd-utf16.xml": '<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="&x;"/></testsuite>'.encode("utf-16"),
        "external.xml": b'<!DOCTYPE testsuite SYSTEM "file:///tmp/probe-secret"><testsuite><testcase name="a"/></testsuite>',
        injection_name: (
            '<testsuite name="suite 👋&#10;::notice::suite">'
            '<testcase name="한글 👋&#13;&#10;::error::forged%0A"/>'
            '<testcase name="한글 👋&#13;&#10;::error::forged%0A"/>'
            '</testsuite>'
        ).encode("utf-8"),
    }
    for name, payload in payloads.items():
        (temp / name).write_bytes(payload)
        (temp / name).chmod(0o644)
    if args.container:
        base = ["docker", "run", "--rm", "--platform", "linux/amd64", "--read-only",
                "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
                "--mount", "type=bind,source=" + str(root) + ",target=/work,readonly",
                "--mount", "type=bind,source=" + str(temp) + ",target=/fixtures,readonly",
                "--workdir", "/work"]
        prefix = base + [args.container]
        fixture = lambda name: "/work/examples/" + name
        extra = lambda name: "/fixtures/" + name
    else:
        prefix = [str(Path(args.console).absolute())]
        fixture = lambda name: str(root / "examples" / name)
        extra = lambda name: str(temp / name)

    def run(arguments, expected, label, report=True, github=False):
        command_prefix = prefix
        command_env = clean_env
        if github:
            command_env = dict(clean_env, PYTHONIOENCODING="cp949")
            if args.container:
                command_prefix = base + ["--env", "PYTHONIOENCODING=cp949", args.container]
        result = subprocess.run(command_prefix + arguments, cwd=temp, env=command_env,
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert result.returncode == expected, (label, expected, result.returncode, result.stdout, result.stderr)
        if github:
            assert result.stderr == "", (label, result.stderr)
        checks.append(label)
        return json.loads(result.stdout) if report else result.stdout

    def commands(content):
        # Parse physical command boundaries before decoding, as Actions does.
        assert content.endswith("\n") and "\r" not in content, content
        parsed = []
        for line in content[:-1].split("\n"):
            match = re.fullmatch(r"::(error|notice) ([^:\r\n]*)::([^\r\n]*)", line)
            assert match is not None, line
            level, raw_properties, raw_message = match.groups()
            properties = {}
            for item in raw_properties.split(","):
                key, value = item.split("=", 1)
                assert key in {"title", "file"} and key not in properties, item
                replacements = {"25": "%", "0D": "\r", "0A": "\n", "3A": ":", "2C": ","}
                properties[key] = re.sub(r"%(25|0D|0A|3A|2C)", lambda token: replacements[token[1]], value)
            replacements = {"25": "%", "0D": "\r", "0A": "\n"}
            message = re.sub(r"%(25|0D|0A)", lambda token: replacements[token[1]], raw_message)
            parsed.append((level, properties, message))
        return parsed

    assert run(["--version"], 0, "console-version", False).strip() == version
    green = run([fixture("green.xml"), "--min-executed", "2", "--max-skipped", "1"], 0, "valid-report")
    assert green["accepted"] and green["counts"]["executed"] == 2 and green["counts"]["skipped"] == 1
    contradictory = run([fixture("contradictory.xml")], 1, "inflated-count-rejected")
    assert any(issue["code"] == "evidence.count_mismatch" for issue in contradictory["issues"])
    failed = run([fixture("failed.xml")], 1, "failed-and-error-records")
    assert failed["counts"]["failed"] == 1 and failed["counts"]["errors"] == 1
    malformed = run([extra("malformed.xml")], 2, "malformed-input")
    assert any(issue["code"] == "input.invalid_xml" for issue in malformed["issues"])
    for name in ("dtd-utf8.xml", "dtd-utf16.xml", "external.xml"):
        unsafe = run([extra(name)], 2, name)
        assert any(issue["code"] == "input.unsafe_xml" for issue in unsafe["issues"])
    literal = run([fixture("literal-names.xml")], 0, "literal-name-json")
    markdown = run([fixture("literal-names.xml"), "--format", "markdown"], 0, "literal-name-markdown", False)
    row = markdown.splitlines()[-1]
    assert row.count("|") == 5 and "<img>" not in row
    cells = [html.unescape(cell.strip().replace("<br>", "\n")) for cell in row.split("|")[1:-1]]
    original = literal["cases"][-1]
    assert cells == [" / ".join(original["suite"]), original["classname"], original["name"], original["status"]], cells
    run([fixture("green.xml"), "--max-bytes", "1"], 2, "bounded-input")
    run([fixture("green.xml"), "--min-executed", "-1"], 2, "invalid-policy", False)

    github_green = commands(run([fixture("green.xml"), "--format", "github"], 0,
                                "github-success-under-legacy-encoding", False, github=True))
    assert github_green == [("notice", {"title": "JUnit evidence: PASS"},
                             "2 executed; 2 passed; 0 failed; 0 errors; 1 skipped; 3 unique of 3 records; exit 0")]
    github_rejection = commands(run([fixture("contradictory.xml"), "--format", "github"], 1,
                                    "github-rejection-counts-and-source", False, github=True))
    assert github_rejection == [
        ("error", {"title": "JUnit evidence: evidence.count_mismatch", "file": fixture("contradictory.xml")},
         "testsuite inflated declares tests=39; observed 1"),
        ("notice", {"title": "JUnit evidence: REJECT"},
         "1 executed; 1 passed; 0 failed; 0 errors; 0 skipped; 1 unique of 1 records; exit 1"),
    ]
    github_malformed = commands(run([extra("malformed.xml"), "--format", "github"], 2,
                                    "github-input-error-counts-and-source", False, github=True))
    assert github_malformed == [
        ("error", {"title": "JUnit evidence: input.invalid_xml", "file": extra("malformed.xml")},
         "Malformed or unsupported XML encoding"),
        ("error", {"title": "JUnit evidence: policy.zero_executed"},
         "No non-skipped testcase evidence was recorded"),
        ("notice", {"title": "JUnit evidence: REJECT"},
         "0 executed; 0 passed; 0 failed; 0 errors; 0 skipped; 0 unique of 0 records; exit 2"),
    ]
    unsafe_commands = run([extra(injection_name), "--format", "github"], 1,
                          "github-unicode-xml-command-boundary", False, github=True)
    assert "한글 👋%0D%0A::error::forged%250A" in unsafe_commands, unsafe_commands
    assert "github-👋%2C%250A-case.xml" in unsafe_commands, unsafe_commands
    assert commands(unsafe_commands) == [
        ("error", {"title": "JUnit evidence: evidence.duplicate", "file": extra(injection_name)},
         "Repeated testcase identity: ::" + injection_case + " in " + injection_suite),
        ("notice", {"title": "JUnit evidence: REJECT"},
         "1 executed; 1 passed; 0 failed; 0 errors; 0 skipped; 1 unique of 2 records; exit 1"),
    ]
    missing_name = "missing:👋,%0A.xml"
    unmatched = run([missing_name, "--format", "github"], 2,
                    "github-unmatched-path-property-escaping", False, github=True)
    assert "file=missing%3A👋%2C%250A.xml" in unmatched, unmatched
    assert commands(unmatched) == [
        ("error", {"title": "JUnit evidence: input.missing"}, "No report files supplied"),
        ("error", {"title": "JUnit evidence: policy.zero_executed"},
         "No non-skipped testcase evidence was recorded"),
        ("error", {"title": "JUnit evidence: input.unmatched", "file": missing_name},
         "Path or glob pattern matched no files"),
        ("notice", {"title": "JUnit evidence: REJECT"},
         "0 executed; 0 passed; 0 failed; 0 errors; 0 skipped; 0 unique of 0 records; exit 2"),
    ]
    if args.container:
        command = base + ["--entrypoint", "python", args.container, "-P", "-c",
                         "import os,junit_evidence_gate; assert os.getuid()==10001; assert junit_evidence_gate.__file__.startswith('/app/'); print(os.getuid())"]
        identity = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert identity.returncode == 0 and identity.stdout.strip() == "10001", identity
        checks.append("non-root-and-mounted-source-independent-import")
    else:
        command = [str(Path(args.python).absolute()), "-I", "-c",
                   "import json,sys,junit_evidence_gate; print(json.dumps({'path':junit_evidence_gate.__file__,'prefix':sys.prefix,'version':junit_evidence_gate.__version__}))"]
        identity = subprocess.run(command, cwd=temp, env=clean_env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=30)
        assert identity.returncode == 0, identity.stderr
        imported = json.loads(identity.stdout)
        environment = str(Path(args.python).absolute().parents[1])
        assert os.path.commonpath([imported["path"], environment]) == environment, imported
        assert imported["version"] == version and os.path.normcase(imported["prefix"]) == os.path.normcase(environment), imported
        checks.append("isolated-wheel-import-outside-source")
        assert subprocess.run([args.python, "-I", "-m", "junit_evidence_gate", "--version"],
                              cwd=temp, env=clean_env, capture_output=True, text=True, timeout=30).stdout.strip() == version
        checks.append("module-version")
print(json.dumps({"mode": "container" if args.container else "wheel", "version": version, "checks": checks, "passed": len(checks)}, indent=2))
