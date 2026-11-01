"""Pinned Jest/jest-junit reports exercise real producer conventions offline."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from junit_evidence_gate import Policy, inspect_reports
from junit_evidence_gate.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'examples/producers/jest'
SPEC = importlib.util.spec_from_file_location('jest_producer', ROOT / 'integrations/jest/run.py')
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


class JestReportTests(unittest.TestCase):
    def test_passing_jest_report_meets_execution_and_skip_policy(self):
        report = inspect_reports([FIXTURES / 'passing.xml'], Policy(min_executed=2, max_skipped=1))
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts, {'records': 3, 'unique': 3, 'executed': 2,
                                       'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 1})
        self.assertEqual(report.issues, [])

    def test_failed_jest_assertion_rejects_with_correct_counts(self):
        report = inspect_reports([FIXTURES / 'failing.xml'])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['passed'], 1)
        self.assertEqual(report.counts['failed'], 1)
        self.assertEqual({issue.code for issue in report.issues}, {'policy.failed'})

    def test_todo_report_inconsistency_cannot_pass(self):
        report = inspect_reports([FIXTURES / 'todo.xml'])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['records'], 2)
        self.assertEqual({issue.code for issue in report.issues}, {'evidence.count_mismatch'})
        # The producer omits a skipped marker for todo. These counts describe
        # rejected XML records, not successful executions of a todo test body.
        self.assertEqual(report.counts['skipped'], 0)
        self.assertEqual(len(report.issues), 2)

    def test_suite_loading_error_keeps_duplicate_and_count_guards(self):
        report = inspect_reports([FIXTURES / 'suite-error.xml'])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['records'], 2)
        self.assertEqual(report.counts['unique'], 1)
        self.assertEqual(report.counts['errors'], 1)
        self.assertEqual({issue.code for issue in report.issues},
                         {'evidence.count_mismatch', 'evidence.duplicate', 'policy.failed'})

    def test_corrupted_producer_total_does_not_change_observed_records(self):
        valid = inspect_reports([FIXTURES / 'passing.xml'])
        corrupted = inspect_reports([FIXTURES / 'contradictory.xml'])
        self.assertEqual(corrupted.exit_code, 1)
        self.assertEqual(corrupted.counts, valid.counts)
        self.assertEqual({issue.code for issue in corrupted.issues}, {'evidence.count_mismatch'})
        self.assertEqual(len(corrupted.issues), 1)

    def test_each_fixture_has_the_expected_cli_exit(self):
        for name, expected in (('passing', 0), ('failing', 1), ('todo', 1),
                               ('suite-error', 1), ('contradictory', 1)):
            with self.subTest(name=name):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main([str(FIXTURES / (name + '.xml'))])
                report = json.loads(output.getvalue())
                self.assertEqual(code, expected)
                self.assertEqual(report['schema_version'], 1)
                self.assertEqual(report['accepted'], expected == 0)

    def test_fixture_normalization_is_idempotent_and_removes_diagnostics(self):
        for name in ('passing', 'failing', 'todo', 'suite-error', 'contradictory'):
            with self.subTest(name=name):
                data = (FIXTURES / (name + '.xml')).read_bytes()
                self.assertEqual(producer.normalized(data), data)
                self.assertNotIn(str(ROOT).encode(), data)
                root = ET.fromstring(data)
                for node in root.iter():
                    self.assertFalse({'time', 'timestamp', 'hostname'} & node.attrib.keys())
                    if node.tag in {'failure', 'error'}:
                        self.assertIsNone(node.text)

    def test_fixture_check_does_not_replace_mismatches_without_explicit_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            target = folder / 'passing.xml'
            target.write_bytes(b'existing fixture')
            with patch.object(producer, 'FIXTURES', folder):
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    producer.check_fixture('passing', b'new fixture', False)
                self.assertEqual(target.read_bytes(), b'existing fixture')
                producer.check_fixture('passing', b'new fixture', True)
                self.assertEqual(target.read_bytes(), b'new fixture')

    def test_normalization_handles_known_windows_paths_without_rewriting_names(self):
        raw = b'<testsuite name="cases\\suite-error.test.js"><testcase name="ordinary\\name"/></testsuite>'
        root = ET.fromstring(producer.normalized(raw))
        self.assertEqual(root.get('name'), 'cases/suite-error.test.js')
        self.assertEqual(root.find('testcase').get('name'), 'ordinary\\name')

    def test_normalization_preserves_status_records_and_declared_counts(self):
        raw = (
            '<testsuites tests="2" failures="1" time="0.2">'
            '<testsuite name="unit" tests="2" failures="1" timestamp="2026-10-04">'
            '<testcase name="ok" time="0.1"/>'
            '<testcase name="bad"><failure>private diagnostic text</failure></testcase>'
            '</testsuite></testsuites>'
        ).encode()
        normalized = producer.normalized(raw)
        self.assertNotIn(b'private diagnostic text', normalized)
        with tempfile.TemporaryDirectory() as temporary:
            before = Path(temporary) / 'before.xml'
            after = Path(temporary) / 'after.xml'
            before.write_bytes(raw)
            after.write_bytes(normalized)
            original = inspect_reports([before])
            sanitized = inspect_reports([after])
        self.assertEqual(original.counts, sanitized.counts)
        self.assertEqual(original.exit_code, sanitized.exit_code)
        self.assertEqual([i.code for i in original.issues], [i.code for i in sanitized.issues])


if __name__ == '__main__':
    unittest.main()
