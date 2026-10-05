# Real Maven Surefire reports

The committed fixtures come from the synthetic Java tests in
`integrations/surefire`, using Maven **3.10.0**, Maven Surefire **3.6.0**,
Maven Compiler Plugin **3.14.1**, and JUnit Jupiter **6.1.3** on Java 21.
The producer is optional: the Python gate still has no runtime dependencies.

## Check the reports without Java

```sh
python -m junit_evidence_gate examples/producers/surefire/passing.xml --min-executed 2 --max-skipped 1
python -m junit_evidence_gate examples/producers/surefire/parameterized.xml --min-executed 2
python -m junit_evidence_gate examples/producers/surefire/failing.xml
python -m junit_evidence_gate examples/producers/surefire/contradictory.xml
python -m junit_evidence_gate examples/producers/surefire/duplicate.xml --min-executed 3
python -m junit_evidence_gate examples/producers/surefire/flaky.xml
python -m junit_evidence_gate examples/producers/surefire/retry-failure.xml
```

The first two commands return `0`. The other commands return `1`:

- `passing.xml` contains two passes and one JUnit `@Disabled` skip
- `parameterized.xml` contains two executed invocations with distinct testcase
  names, so neither invocation is discarded as a duplicate
- `failing.xml` contains a pass, an assertion failure, and an exception error;
  the gate retains the distinction between `failed` and `errors`
- `contradictory.xml` deliberately inflates the passing report's declared total
  and is rejected with `evidence.count_mismatch`
- `duplicate.xml` deliberately copies a passed testcase and adjusts the declared
  total. It is rejected with `evidence.duplicate`; the repeated identity does
  not increase the two executed tests toward `--min-executed 3`

The contradictory and duplicate files are intentional mutations of the passing producer report,
not direct Surefire output. All seven are compatibility fixtures, not evidence
of this repository's own test run.

## Reproduce with the real producer

Install [Maven 3.10.0](https://maven.apache.org/download.cgi) and a Java 21 JDK,
then run from the repository root:

```sh
python integrations/surefire/run.py --keep-reports integrations/surefire/generated
```

Use `--maven /path/to/apache-maven-3.10.0/bin/mvn` when Maven is not on PATH.
Dependencies are resolved from Maven Central into an ignored, integration-local
cache. Pass `--repository /path/to/cache` to select another cache. Once it is
populated, `--offline` reruns the scenarios without downloading dependencies.
For networks that need a Maven proxy, pass your own `--settings /path/to/settings.xml`;
that file is not copied into fixtures. Do not commit credentials or proxy settings.

The generator verifies the pinned Maven, plugin, and JUnit versions before
running five fresh temporary Java projects. It checks Maven's exit status,
report counts, and gate exit status for each scenario, then compares the
sanitized report bytes with the committed fixtures. The intentionally failing
Java class must cause Maven to exit `1`; that expected failure does not make
the generator fail. All seven evidence checks must succeed before
`--update-fixtures` writes an intentional fixture refresh.

Sanitization removes the report's environment properties, timing/host attributes,
and failure/output text, and sorts testcase records by identity. Counts,
result markers, and the synthetic failure messages stay intact. Gate counts,
issue codes, and exit status must be identical before and after sanitization.
`--keep-reports` keeps only sanitized XML and path-redacted Maven logs, never
raw XML environment properties. This normalizer is for these synthetic fixtures;
it is not a general-purpose sanitizer for private test reports.

The [Surefire workflow](../.github/workflows/surefire-producer.yml) runs the live
producer for relevant changes. It pins its actions, verifies the Maven download
with SHA-512, uses read-only repository permissions, and retains sanitized
reports and synthetic runner logs for seven days. It does not publish packages.
The normal Python suite tests all seven committed reports without requiring
Java, Maven, or network access.

## Scope and limits

Always keep the Maven runner's exit status in CI. XML cannot prove that a
build completed, that every expected class was discovered, or that a process
was not interrupted. These fixtures cover Jupiter passes, disabled tests,
assertion failures, exceptions, and parameterized invocations. They also verify
the gate's strict rejection of Surefire's extended retry format:

- `flaky.xml` comes from two tests that deterministically fail/error on their
  first invocation and pass on retry. Maven returns `0`, but the gate returns `1`
  with `evidence.unsupported_result` for `flakyFailure` and `flakyError`
- `retry-failure.xml` comes from two tests that still fail/error after retry.
  Maven and the gate return `1`; the gate reports `evidence.unsupported_result`
  for `rerunFailure`/`rerunError`, plus `policy.failed` for the original results

Both scenarios use `-Dsurefire.rerunFailingTestsCount=1`. Retry attempts are not
additional testcase identities. The gate does not interpret extended retry
records as a supported final outcome: diagnostic counts on a rejected report
are not a flaky-test acceptance policy. See [report conventions](compatibility.md)
before normalizing an extended producer format. No tolerant retry mode is added,
and these fixtures do not establish compatibility with every JUnit engine.
Duplicate testcase identities also remain an error.

- [Surefire JUnit Platform documentation](https://maven.apache.org/surefire/maven-surefire-plugin/examples/junit-platform.html)
- [Surefire single-test selection](https://maven.apache.org/surefire/maven-surefire-plugin/examples/single-test.html)
- [Surefire retry report format](https://maven.apache.org/surefire/maven-surefire-plugin/examples/rerun-failing-tests.html)
- [JUnit user guide](https://docs.junit.org/current/user-guide/)

The Java tests are original synthetic examples. Maven, Surefire, and JUnit
retain their own licenses and are optional development dependencies; their
binaries are not included in this Python package.
