# GitHub Actions annotations

`--format github` puts the gate's check reasons into the Actions log and Checks annotations. It uses workflow commands on stdout and needs no API token or extra runtime package.

## Try the included fixture

After installing a checkout:

```sh
python -m pip install .
junit-evidence-gate examples/green.xml --min-executed 2 --max-skipped 1 --format github
junit-evidence-gate examples/contradictory.xml --format github
```

The first command exits `0` and emits a count notice. The second exits `1` and emits an error titled `JUnit evidence: evidence.count_mismatch`. Its detail is `testsuite inflated declares tests=39; observed 1`.

To see the notice in GitHub, save this as `.github/workflows/evidence-example.yml` in a checkout of this repository:

```yaml
name: JUnit evidence example

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  evidence:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
        with:
          python-version: '3.12'
      - name: Install the gate
        run: python -m pip install .
      - name: Check the included report
        run: junit-evidence-gate examples/green.xml --min-executed 2 --max-skipped 1 --format github
```

Open the Actions tab, select **JUnit evidence example**, and run it. The notice reports two executed cases and one skip. Replace `green.xml` with `contradictory.xml` to see the rejection annotation and failed step.

## Check reports from your test runner

Install the gate in the same environment as your runner, then keep these as separate steps:

```yaml
- name: Run tests
  run: python -m pytest --junit-xml=report.xml
- name: Annotate recorded evidence
  if: always()
  run: junit-evidence-gate report.xml --min-executed 20 --max-skipped 2 --format github
```

Use an execution minimum and skip budget that fit your project. `if: always()` exposes a missing or invalid report after the runner fails. Keep the runner step's own exit status; it catches collection errors and interrupted runs that may leave incomplete XML. Run one gate per matrix environment.

Use stdout for annotations. Saving commands with `--output` creates a text file; it does not display annotations until the commands are printed into a step log. Keep JSON or Markdown as a separate artifact when you need the full structured records or table.

## Output contract

- Each `Report.issues` entry produces one `::error` command. Its title contains the stable issue code and its message contains the existing check detail.
- A nonempty issue source adds a `file` property that names the original XML report or unmatched input. Testcase file and line attributes are not used as source-code locations.
- One final `::notice` command contains the verdict, counts, and exit code, including input-error exit `2`.
- Every command occupies one physical line and the result ends with a newline. The formatter keeps issue order and does not mutate the report.
- JSON remains the default format with schema version `1`. Policy checks, duplicate identities, input limits, and exit codes are shared by all formats.

The encoder follows the [GitHub Actions workflow-command syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands) and the [Actions toolkit implementation](https://github.com/actions/toolkit/blob/6cb87687384f971ebb756e43c7a56f62cd80a31d/packages/core/src/command.ts). Command data escapes `%`, CR, and LF. Property values additionally escape `:` and `,`. Percent signs are encoded first so literal text such as `%0A` stays literal after the runner decodes it. Suite and testcase names may appear in check details; the formatter applies the same encoding to those details and to source paths.

The runner controls how many annotations it displays. JSON output retains the complete issue and testcase lists. Failure messages, stack traces, and captured test output are not collected for summaries.

## Python API

```python
import sys
from junit_evidence_gate import Policy, inspect_reports, render_github

report = inspect_reports(["report.xml"], Policy(min_executed=20, max_skipped=2))
sys.stdout.write(render_github(report))
raise SystemExit(report.exit_code)
```

`render_github(report)` returns a string. It performs no network or file operations. `inspect_reports` remains responsible for bounded XML parsing and the acceptance policy; your caller controls where the rendered text is written and whether to exit.
