# Real Jest producer reports

The committed fixtures were generated from the synthetic tests in
`integrations/jest` using Jest **30.5.2**, jest-junit **17.0.0**, and Node **24.19.0**.
The versions are pinned in the optional integration's package and lock files.
The Python gate keeps its standard-library-only runtime.

## Check the reports without Node

```sh
python -m junit_evidence_gate examples/producers/jest/passing.xml --min-executed 2 --max-skipped 1
python -m junit_evidence_gate examples/producers/jest/failing.xml
python -m junit_evidence_gate examples/producers/jest/contradictory.xml
```

The first command returns `0`; the other two return `1`. The passing report has
two passed records and one skipped record. The failing report has one passed
record and one failed record. `contradictory.xml` is an intentional mutation of
the passing report's root `tests` total, demonstrating count-mismatch detection.

These files are compatibility fixtures, separate from evidence of this
repository's own test run.

## Reproduce with the real producer

Install Node 24, then run from the repository root:

```sh
cd integrations/jest
npm ci --ignore-scripts --no-audit --no-fund
cd ../..
python integrations/jest/run.py --keep-reports integrations/jest/generated
```

The generator runs four real Jest invocations, checks each runner exit status,
passes each fresh report through the gate, and verifies that sanitized output
matches the committed fixtures. Intentional assertion/module-loading failures
are expected scenarios; the generator succeeds only when their reports are
rejected for the expected reasons. A fifth check inflates the passing report's
declared total without adding any records.

After reviewing an intentional producer change, add `--update-fixtures` to
replace the fixture files. All producer scenarios must pass their verification
before any committed fixture is updated. The raw XML and runner logs retained
with `--keep-reports` stay in the ignored generated directory. The separate
[Jest workflow](../.github/workflows/jest-producer.yml) runs this integration for
relevant changes and keeps those synthetic reports for seven days.

Sanitization removes timing/host attributes and failure/output text, and
normalizes any checkout-path prefix in attributes. Testcase records, result
markers, and declared counts stay intact. The generator verifies equal gate
counts, issue codes, and exit status before and after sanitization.

## Observed compatibility limits

With these pinned producer versions:

- `test.skip` produces a skipped testcase and consistent totals
- A failed assertion produces a failed testcase and the gate returns `1`
- `test.todo` produces a testcase without a skipped marker, but is excluded from
  the declared totals. Jest can exit `0` while the gate correctly rejects the
  inconsistent XML with `evidence.count_mismatch`. Its diagnostic record counts
  must not be interpreted as execution of the absent todo body
- With `reportTestSuiteErrors: "true"`, a module-loading error produces two
  records sharing one identity and zero declared tests. The gate rejects the
  duplicate, inconsistent totals, and recorded error

These limits are recorded in `todo.xml` and `suite-error.xml`; the fixtures do
not weaken counting or duplicate policy to accommodate inconsistent reports.
A related upstream TODO proposal is [jest-junit PR 261](https://github.com/jest-community/jest-junit/pull/261).

Always preserve the Jest runner's exit status in CI. The report can omit
information about loading, interruption, or execution, especially with different
reporter options. The optional integration enables suite-loading error reporting
so that its failure case remains visible.

## Producer documentation

- [Jest CLI](https://jestjs.io/docs/cli): serial execution, CI mode, and exact test-file selection
- [jest-junit configuration](https://github.com/jest-community/jest-junit#configuration): reporter options and suite-loading errors

The test sources in this repository are synthetic. Dependencies retain their
own licenses; Jest and jest-junit are optional development tools rather than
vendored runtime code.
