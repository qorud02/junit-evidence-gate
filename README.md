# JUnit Evidence Gate

Reject a green CI report when its testcase records are empty, duplicated, contradictory, or failing.

```sh
python -m junit_evidence_gate examples/contradictory.xml --format markdown
```

This fixture declares 39 tests and contains one testcase. The gate returns exit code `1` and reports `testsuite inflated declares tests=39; observed 1`.

The small CLI counts testcase records, compares them with declared suite totals, and applies an execution minimum and optional skip budget. It runs offline with Python 3.10+ and has no runtime dependencies.

## Run it

From a checkout:

```sh
python -m junit_evidence_gate examples/green.xml
python -m junit_evidence_gate examples/nested.xml --min-executed 3
python examples/run_demo.py
python -m unittest discover -s tests -v
```

Install the local package to use its command anywhere:

```sh
python -m pip install .
junit-evidence-gate "reports/**/*.xml" --min-executed 20 --max-skipped 2
```

Quote glob patterns so expansion behaves consistently across shells. Every supplied path or pattern must match a readable report. Repeating the same file path reads that file once.

## What the gate checks

| Check | Behavior |
| --- | --- |
| No recorded execution | Rejects empty and skipped-only reports, including with `--min-executed 0`. |
| Suite totals | Compares `tests`, `failures`, `errors`, and `skipped` attributes with descendant testcase records. Missing totals are computed from records. |
| Nested suites | Counts each testcase once. Parent totals cover all descendant testcases. |
| Duplicate identities | Rejects repeated `(suite path, classname, name)` identities within or across reports. Duplicate records never increase the execution minimum. |
| Misplaced results | Rejects result elements on suites or hidden inside metadata, without inventing testcase records. |
| Failure and error | Rejects any recorded failure/error. Multiple failure elements in one testcase count as one failing testcase. |
| Skip budget | Optional maximum count and ratio use unique testcase identities. |
| Missing names and conflicts | Rejects blank testcase names, unknown status values, and contradictory skip/result declarations. |
| Unsafe or malformed XML | Rejects DTDs, declared entities, invalid roots, and files exceeding byte, depth, or node limits. |

`executed` counts unique non-skipped testcase records: passed + failed + errors. A setup error can therefore appear in this measure. Run logs, the runner exit code, and the CI job identify the execution that produced those records. Zero-duration tests are valid, and duration alone is not used as execution evidence.

Run the gate separately for each environment or CI matrix job. Test retries or combined matrix artifacts often reuse testcase identities; select the intended final report before checking. The [compatibility notes](docs/compatibility.md) describe supported conventions.

## Exit codes and output

| Exit | Meaning |
| --- | --- |
| `0` | All supplied reports meet the policy. |
| `1` | Evidence or policy check rejected the report. |
| `2` | Input, CLI option, parsing, or output error. Input errors take priority. |

JSON is the default. It contains the policy, actual counts, source paths, testcase records, and stable issue codes under `schema_version: 1`.

```sh
junit-evidence-gate "reports/*.xml" --max-skip-ratio 0.05 --output evidence.json
junit-evidence-gate reports/unit.xml --format markdown --output evidence.md
```

Markdown escapes testcase names, suite names, file paths, and check details so HTML, pipes, emphasis, code spans, and link-like text display literally. Newlines become line breaks inside a table cell. Test output and failure stack traces are omitted from summaries.

Default input limits are 5 MiB per file, 100,000 XML nodes per file, and a depth of 100. The CLI exposes `--max-bytes`; the Python API also accepts node and depth limits. XML stays local. An output file cannot overwrite an input report.

## Add to CI

Generate the report with your existing runner, preserve its exit status, and run the gate in the same job:

```yaml
- name: Run tests
  run: python -m pytest --junit-xml=report.xml
- name: Check recorded evidence
  if: always()
  run: python -m junit_evidence_gate report.xml --min-executed 20 --max-skipped 2 --format markdown --output evidence.md
- name: Attach evidence
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: junit-evidence
    path: |
      report.xml
      evidence.md
```

Install this package before these steps. `if: always()` makes missing evidence visible after an earlier failure. Keep the runner step in the job: it catches collection failures, interrupted runs, and errors that XML alone may omit.

The repository's own [CI definition](.github/workflows/ci.yml) installs the package, runs the unit and CLI tests, exercises all eight fixtures, and checks the installed command on Windows and Linux with Python 3.10–3.13.

## Python API

```python
from junit_evidence_gate import Policy, inspect_reports

report = inspect_reports(["report.xml"], Policy(min_executed=20, max_skipped=2))
print(report.counts)
for issue in report.issues:
    print(issue.code, issue.message)
raise SystemExit(report.exit_code)
```

## Related tools

- [junitparser](https://github.com/weiwei/junitparser) provides an API for parsing, creating, editing, and merging JUnit XML. Its documented merge behavior leaves duplicate checking to the caller. This CLI provides one ready-to-run acceptance policy over the records.
- [test-summary/action](https://github.com/test-summary/action) produces GitHub Actions summaries and count outputs from JUnit XML and TAP. Use it for presentation; this CLI adds strict consistency and execution/skip checks before accepting reports.
- [pytest JUnit XML output](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files) produces reports from pytest. This CLI reads reports produced by pytest and other compatible runners.

See [CONTRIBUTING.md](CONTRIBUTING.md) for focused contributions and reproducible fixture requirements.
