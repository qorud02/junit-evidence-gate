"""Exercise installed wheel or non-root container before publishing."""
import argparse
import html
import json
import os
from pathlib import Path
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
    temp = Path(directory)
    temp.chmod(0o755)
    payloads = {
        "malformed.xml": b"<testsuite>",
        "dtd-utf8.xml": b'<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="&x;"/></testsuite>',
        "dtd-utf16.xml": '<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="&x;"/></testsuite>'.encode("utf-16"),
        "external.xml": b'<!DOCTYPE testsuite SYSTEM "file:///tmp/probe-secret"><testsuite><testcase name="a"/></testsuite>',
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

    def run(arguments, expected, label, report=True):
        result = subprocess.run(prefix + arguments, cwd=temp, env=clean_env,
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert result.returncode == expected, (label, expected, result.returncode, result.stdout, result.stderr)
        checks.append(label)
        return json.loads(result.stdout) if report else result.stdout

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
