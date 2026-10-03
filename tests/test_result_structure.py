"""Reserved result elements must not disappear into report metadata."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from junit_evidence_gate import inspect_reports
from junit_evidence_gate.cli import main


class ResultStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "report.xml"

    def report(self, xml):
        self.path.write_text(xml, encoding="utf-8")
        return inspect_reports([self.path])

    def assert_rejected_structure(self, xml, records=1, passed=1):
        report = self.report(xml)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("evidence.structure", {issue.code for issue in report.issues})
        self.assertEqual(report.counts["records"], records)
        self.assertEqual(report.counts["passed"], passed)
        return report

    def test_suite_and_group_results_cannot_be_ignored(self):
        for marker in ("error", "failure", "skipped"):
            for namespace in ("", ' xmlns="urn:junit"'):
                for xml in (
                    f'<testsuite{namespace}><testcase name="ok"/><{marker}/></testsuite>',
                    f'<testsuites{namespace}><testsuite><testcase name="ok"/></testsuite><{marker}/></testsuites>',
                ):
                    with self.subTest(marker=marker, xml=xml):
                        self.assert_rejected_structure(xml)

    def test_report_metadata_cannot_hide_results(self):
        for marker in ("error", "failure", "skipped"):
            for wrapper in ("properties", "system-out", "system-err", "producer-metadata"):
                with self.subTest(marker=marker, wrapper=wrapper):
                    self.assert_rejected_structure(
                        f'<testsuite><testcase name="ok"/><{wrapper}><detail><{marker}/></detail></{wrapper}></testsuite>'
                    )

    def test_testcase_metadata_cannot_hide_results(self):
        for marker in ("error", "failure", "skipped"):
            for wrapper in ("properties", "system-out", "system-err"):
                with self.subTest(marker=marker, wrapper=wrapper):
                    self.assert_rejected_structure(
                        f'<testsuite xmlns="urn:junit"><testcase name="ok"><{wrapper}><detail><{marker}/></detail></{wrapper}></testcase></testsuite>'
                    )

    def test_result_children_cannot_hide_another_result(self):
        for outer in ("error", "failure", "skipped"):
            for inner in ("error", "failure", "skipped"):
                with self.subTest(outer=outer, inner=inner):
                    report = self.assert_rejected_structure(
                        f'<testsuite><testcase name="ok"/><testcase name="bad"><{outer}><{inner}/></{outer}></testcase></testsuite>',
                        records=2,
                    )
                    self.assertEqual(report.counts["skipped"], int(outer == "skipped"))
                    self.assertEqual(report.counts["failed"], int(outer == "failure"))
                    self.assertEqual(report.counts["errors"], int(outer == "error"))

    def test_plain_metadata_and_literal_xml_text_remain_supported(self):
        for namespace in ("", ' xmlns="urn:junit"'):
            with self.subTest(namespace=namespace):
                report = self.report(
                    f'<testsuite{namespace}><properties><property name="error" value="failure"/></properties>'
                    '<producer-metadata><detail>skipped</detail></producer-metadata>'
                    '<system-out><![CDATA[<error><failure/></error>]]></system-out>'
                    '<testcase name="ok"><properties><property name="skipped" value="error"/></properties>'
                    '<system-out>&lt;failure/&gt;</system-out>'
                    '<system-err><![CDATA[<skipped/>]]></system-err></testcase></testsuite>'
                )
                self.assertEqual(report.exit_code, 0)
                self.assertEqual(report.counts["passed"], 1)
                self.assertEqual(report.issues, [])

    def test_direct_testcase_results_keep_existing_counts(self):
        report = self.report(
            '<testsuite xmlns="urn:junit" tests="4" failures="1" errors="1" skipped="1">'
            '<testcase name="ok"/><testcase name="failed"><failure/><failure/></testcase>'
            '<testcase name="errored"><error/></testcase><testcase name="skipped"><skipped/></testcase></testsuite>'
        )
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts, {
            "records": 4, "unique": 4, "executed": 3, "passed": 1,
            "failed": 1, "errors": 1, "skipped": 1,
        })
        codes = {issue.code for issue in report.issues}
        self.assertNotIn("evidence.structure", codes)
        self.assertNotIn("evidence.count_mismatch", codes)

    def test_cli_rejects_misplaced_error_without_changing_schema(self):
        self.path.write_text(
            '<testsuite><testcase name="ok"/><error message="setup failed"/></testsuite>',
            encoding="utf-8",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([str(self.path)])
        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["schema_version"], 1)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["counts"]["passed"], 1)
        self.assertEqual(report["counts"]["errors"], 0)
        self.assertIn("evidence.structure", {issue["code"] for issue in report["issues"]})

    def test_input_error_still_takes_priority(self):
        self.path.write_text(
            '<testsuite><testcase name="ok"/><failure/></testsuite>', encoding="utf-8"
        )
        report = inspect_reports([self.path, self.path.parent / "missing.xml"])
        self.assertEqual(report.exit_code, 2)
        self.assertIn("evidence.structure", {issue.code for issue in report.issues})
        self.assertIn("input.unreadable", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
