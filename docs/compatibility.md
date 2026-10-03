# Report conventions

The gate accepts `testsuite` and `testsuites` roots, optional XML namespaces, nested suites, and direct testcase children of a `testsuite`. Report metadata such as `properties` and `system-out` stays outside testcase counting.

Each suite's declared totals cover all descendant testcase records. Producers that declare direct-child totals on a parent suite should omit or normalize those parent attributes before checking. An unsupported structure is rejected with `evidence.structure`.

Testcase result children have priority: `error`, then `failure`, then `skipped`. Multiple failure/error elements in a testcase count that testcase once for each respective declared suite measure. The summary gives a testcase with both an error and a failure the `error` status. A skip together with failure/error is contradictory and rejected.

Supported `status` values are empty, `passed`, `pass`, `run`, `completed`, `failed`, `failure`, `error`, `skipped`, `skip`, `notrun`, and `disabled`, ignoring case. `run` and `completed` describe an attempted run and may accompany a result child. Unknown values reject the report. Testcase children may be `failure`, `error`, `skipped`, `properties`, `system-out`, and `system-err`. Other children, including `rerunFailure` and `flakyFailure`, reject the report with `evidence.unsupported_result`. Reduce extended retry formats to their selected final testcase records first.

Suite names form the identity path. Repeated sibling suites with the same name and repeated class/testcase names share an identity. For shards, use stable distinct suite names or give each shard a separate invocation. For matrix jobs, check reports within each job.

The fixtures are small reproducible XML inputs. They demonstrate policy behavior and parser conventions. They are separate from the repository's own test run results.

## Why another command

The existing tools cover broad parsing and useful summaries. This project packages a narrow policy that can be run locally and used as an exit-code gate: nonempty execution records, declared-count consistency, duplicate identities, and skip budgets. A team can also implement these policies on top of a general XML library. This project makes that policy reusable without a hosted service or runtime dependencies.

Sources reviewed on 2026-10-03 Asia/Seoul:

- [junitparser README](https://github.com/weiwei/junitparser): parsing, manipulation, merging, and caller-managed duplicate handling.
- [Test Summary README](https://github.com/test-summary/action): JUnit/TAP summaries, count outputs, and Markdown output.
- [pytest documentation](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files): report generation and result element conventions.
