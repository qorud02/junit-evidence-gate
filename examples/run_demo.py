"""Exercise shipped fixtures through the actual CLI, checking exit codes."""

from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
expectations = {
    "green.xml": (0, 2),
    "skipped-only.xml": (1, 0),
    "contradictory.xml": (1, 1),
    "nested.xml": (0, 3),
    "duplicate.xml": (1, 1),
    "failed.xml": (1, 2),
    "literal-names.xml": (0, 1),
}
results = []
for fixture, (exit_code, executed) in expectations.items():
    result = subprocess.run(
        [sys.executable, "-m", "junit_evidence_gate", str(root / "examples" / fixture)],
        cwd=root, capture_output=True, text=True, check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == exit_code, (fixture, result.returncode, result.stderr)
    assert report["counts"]["executed"] == executed, (fixture, report)
    results.append({"fixture": fixture, "exit_code": result.returncode,
                    "executed": executed, "checks": [issue["code"] for issue in report["issues"]]})
print(json.dumps(results, indent=2))
