# pytest reports

Use the gate to check the testcase records in pytest's JUnit XML. The example below covers passing tests, skips, an expected failure, and `unittest.TestCase` tests collected by pytest.

From a checkout of this repository, install the gate and the report producer:

```sh
python -m pip install . pytest
```

Save this as `test_pytest_gate.py`:

```python
import pytest
import unittest

def test_plain_pass():
    assert 2 + 2 == 4

@pytest.mark.skip(reason="feature disabled")
def test_skip():
    assert False

@pytest.mark.xfail(reason="known regression")
def test_expected_failure():
    assert False

class TestStandardLibrary(unittest.TestCase):
    def test_unittest_pass(self):
        self.assertEqual("abc".upper(), "ABC")

    @unittest.skip("platform-specific")
    def test_unittest_skip(self):
        self.fail("must be skipped")
```

Generate and check each report separately:

```sh
python -m pytest test_pytest_gate.py --junitxml=report-xunit2.xml -o junit_family=xunit2
junit-evidence-gate report-xunit2.xml --min-executed 2 --max-skipped 3

python -m pytest test_pytest_gate.py --junitxml=report-xunit1.xml -o junit_family=xunit1
junit-evidence-gate report-xunit1.xml --min-executed 2 --max-skipped 3
```

Both pytest runs exit `0`. Each gate invocation exits `0` and reports:

| Measure | Count |
| --- | ---: |
| records | 5 |
| unique | 5 |
| executed | 2 |
| passed | 2 |
| failed | 0 |
| errors | 0 |
| skipped | 3 |

The terminal shows two passes, two skips, and one xfail. Pytest records the xfail as a skipped testcase in JUnit XML, so the gate includes it in the skip budget and excludes it from `executed`. Setting `--max-skipped 2` rejects this report with exit `1`; setting `--min-executed 5` also rejects it. Choose those limits for the tests your project expects to record.

## Keep the runner result

In CI, use the [separate runner and gate steps](github-annotations.md#check-reports-from-your-test-runner). Run the gate with `if: always()` and keep the runner step's own exit status. Pytest reports [exit `5` when no tests are collected](https://docs.pytest.org/en/stable/reference/exit-codes.html); an empty JUnit report is also rejected by the gate with exit `1`. Collection errors and interrupted runs must still fail the runner step even if an XML file is present.

Run one gate per runner invocation and matrix environment. The two report families above contain the same testcase identities; combining them into one gate invocation would produce duplicate evidence. For retries, check the selected final report. See [report conventions](compatibility.md) for identities and supported result elements.

Pytest documents [JUnit report generation](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files) and [skip/xfail behavior](https://docs.pytest.org/en/stable/how-to/skipping.html). Pytest is the optional producer used by this example; the gate runs with Python's standard library.
