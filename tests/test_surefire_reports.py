"""Real Surefire fixtures cover skips, failures, errors and parameter identities."""
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from junit_evidence_gate import Policy, inspect_reports
from junit_evidence_gate.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'examples/producers/surefire'
SPEC = importlib.util.spec_from_file_location('surefire_producer', ROOT / 'integrations/surefire/run.py')
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


class SurefireReportTests(unittest.TestCase):
    def test_passing_report_meets_execution_and_skip_policy(self):
        report = inspect_reports([FIXTURES / 'passing.xml'], Policy(min_executed=2, max_skipped=1))
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts, {'records': 3, 'unique': 3, 'executed': 2,
                                       'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 1})

    def test_failures_and_errors_remain_separate(self):
        report = inspect_reports([FIXTURES / 'failing.xml'])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['passed'], 1)
        self.assertEqual(report.counts['failed'], 1)
        self.assertEqual(report.counts['errors'], 1)
        self.assertEqual({issue.code for issue in report.issues}, {'policy.failed'})

    def test_parameterized_invocations_have_distinct_identities(self):
        report = inspect_reports([FIXTURES / 'parameterized.xml'])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts['executed'], 2)
        self.assertEqual(len({case.identity for case in report.cases}), 2)
        self.assertEqual({case.classname for case in report.cases}, {'fixtures.ParameterCasesTest'})

    def test_corrupted_total_is_rejected_without_inventing_cases(self):
        good = inspect_reports([FIXTURES / 'passing.xml'])
        bad = inspect_reports([FIXTURES / 'contradictory.xml'])
        self.assertEqual(bad.exit_code, 1)
        self.assertEqual(bad.counts, good.counts)
        self.assertEqual({issue.code for issue in bad.issues}, {'evidence.count_mismatch'})

    def test_duplicate_record_cannot_inflate_the_execution_minimum(self):
        report = inspect_reports([FIXTURES / 'duplicate.xml'], Policy(min_executed=3))
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['records'], 4)
        self.assertEqual(report.counts['unique'], 3)
        self.assertEqual(report.counts['executed'], 2)
        self.assertEqual({issue.code for issue in report.issues}, {'evidence.duplicate', 'policy.min_executed'})

    def test_passing_after_retry_is_rejected_as_unsupported_evidence(self):
        path = FIXTURES / 'flaky.xml'
        tags = {producer.local(node.tag) for node in ET.parse(path).iter()}
        self.assertTrue({'flakyFailure', 'flakyError'} <= tags)
        report = inspect_reports([path])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['records'], 2)
        self.assertEqual({issue.code for issue in report.issues}, {'evidence.unsupported_result'})

    def test_repeated_failures_do_not_inflate_testcase_counts(self):
        path = FIXTURES / 'retry-failure.xml'
        tags = {producer.local(node.tag) for node in ET.parse(path).iter()}
        self.assertTrue({'failure', 'error', 'rerunFailure', 'rerunError'} <= tags)
        report = inspect_reports([path])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.counts['records'], 2)
        self.assertEqual(report.counts['failed'], 1)
        self.assertEqual(report.counts['errors'], 1)
        self.assertEqual({issue.code for issue in report.issues},
                         {'evidence.unsupported_result', 'policy.failed'})

    def test_relative_maven_launcher_is_resolved_before_temporary_chdir(self):
        launcher = ROOT / 'integrations/surefire/run.py'
        relative = os.path.relpath(launcher)
        with patch.object(producer.shutil, 'which', return_value=None):
            self.assertEqual(producer.maven_launcher(relative), str(launcher.resolve()))

    def test_bare_maven_command_uses_path_lookup(self):
        launcher = ROOT / 'integrations/surefire/run.py'
        with patch.object(producer.shutil, 'which', return_value=str(launcher)):
            self.assertEqual(producer.maven_launcher('mvn'), str(launcher.resolve()))

    def test_all_fixtures_have_expected_cli_results(self):
        for name, expected in (('passing', 0), ('failing', 1), ('parameterized', 0),
                               ('contradictory', 1), ('duplicate', 1), ('flaky', 1), ('retry-failure', 1)):
            with self.subTest(name=name):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main([str(FIXTURES / (name + '.xml'))])
                report = json.loads(output.getvalue())
                self.assertEqual(code, expected)
                self.assertEqual(report['schema_version'], 1)
                self.assertEqual(report['accepted'], expected == 0)

    def test_normalization_is_stable_and_removes_runtime_metadata(self):
        for name in ('passing', 'failing', 'parameterized', 'contradictory', 'duplicate', 'flaky', 'retry-failure'):
            with self.subTest(name=name):
                data = (FIXTURES / (name + '.xml')).read_bytes()
                self.assertEqual(producer.normalized(data), data)
                self.assertNotIn(str(ROOT).encode(), data)
                for node in ET.fromstring(data).iter():
                    self.assertNotEqual(producer.local(node.tag), 'properties')
                    self.assertFalse({'time', 'timestamp', 'hostname'} & node.attrib.keys())
                    if producer.local(node.tag) in {'failure', 'error', 'flakyFailure', 'flakyError', 'rerunFailure', 'rerunError', 'stackTrace'}:
                        self.assertFalse((node.text or "").strip())

    def test_normalization_removes_properties_and_diagnostics_without_changing_evidence(self):
        raw = (
            '<testsuite name="unit" tests="2" failures="1" time="1">'
            '<properties><property name="synthetic-private-setting" value="secret-placeholder"/></properties>'
            '<testcase name="z" time="1"><failure>diagnostic-placeholder</failure></testcase>'
            '<testcase name="a"/></testsuite>'
        ).encode()
        clean = producer.normalized(raw)
        self.assertNotIn(b'secret-placeholder', clean)
        self.assertNotIn(b'diagnostic-placeholder', clean)
        self.assertEqual([node.get('name') for node in ET.fromstring(clean)], ['a', 'z'])
        with tempfile.TemporaryDirectory() as temporary:
            before, after = (Path(temporary) / name for name in ('before.xml', 'after.xml'))
            before.write_bytes(raw)
            after.write_bytes(clean)
            original, sanitized = inspect_reports([before]), inspect_reports([after])
        self.assertEqual(original.counts, sanitized.counts)
        self.assertEqual(original.exit_code, sanitized.exit_code)
        self.assertEqual([i.code for i in original.issues], [i.code for i in sanitized.issues])

    def test_fixture_updates_require_the_explicit_flag(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(producer, 'FIXTURES', Path(temporary)):
            path = Path(temporary) / 'passing.xml'
            path.write_bytes(b'existing fixture')
            with self.assertRaises(RuntimeError):
                producer.check_fixture('passing', b'new fixture', False)
            self.assertEqual(path.read_bytes(), b'existing fixture')
            producer.check_fixture('passing', b'new fixture', True)
            self.assertEqual(path.read_bytes(), b'new fixture')

    @contextmanager
    def fake_producer(self, fault=None, update=False):
        originals = {name: path.read_bytes()
                     for path in FIXTURES.glob('*.xml') for name in [path.stem]}
        class_names = {values[0]: name for name, values in producer.SCENARIOS.items()}
        calls = []

        def fake_run(command, **kwargs):
            if '--version' in command:
                return subprocess.CompletedProcess(command, 0, 'Apache Maven 3.10.0\nJava version: 21.0.1', '')
            name = class_names[next(arg.split('=', 1)[1] for arg in command if arg.startswith('-Dtest='))]
            calls.append(name)
            self.assertEqual('-Dsurefire.rerunFailingTestsCount=1' in command,
                             name in {'flaky', 'retry-failure'})
            reports = kwargs['cwd'] / 'target/surefire-reports'
            reports.mkdir(parents=True)
            data = originals[name]
            if fault == (name, 'evidence'):
                data = data.replace(b'tests="3"', b'tests="999"')
            if fault != (name, 'missing'):
                (reports / 'TEST-synthetic.xml').write_bytes(data)
            if fault == (name, 'extra'):
                (reports / 'TEST-stale.xml').write_bytes(data)
            code = producer.SCENARIOS[name][1]
            if fault == (name, 'exit'):
                code = 1 - code
            return subprocess.CompletedProcess(command, code, 'Synthetic Maven output', '')

        def inspect(path):
            report = inspect_reports([path])
            return {'exit_code': report.exit_code, 'counts': report.counts,
                    'issue_codes': sorted({issue.code for issue in report.issues})}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = root / 'fixtures'
            fixtures.mkdir()
            for name, data in originals.items():
                (fixtures / (name + '.xml')).write_bytes(b'unchanged fixture' if update else data)
            args = ['run.py', '--maven', str(root / 'mvn'), '--repository', str(root / 'cache'), '--offline']
            if update:
                args.append('--update-fixtures')
            with patch.object(sys, 'argv', args), patch.object(producer, 'FIXTURES', fixtures), \
                    patch.object(producer.subprocess, 'run', side_effect=fake_run), \
                    patch.object(producer, 'gate', side_effect=inspect), redirect_stdout(io.StringIO()):
                yield fixtures, calls

    def test_generator_checks_all_scenarios_before_accepting_fixtures(self):
        with self.fake_producer() as (_, calls):
            producer.main()
            self.assertEqual(calls, list(producer.SCENARIOS))

    def test_generator_refuses_wrong_runner_exit_without_refreshing_fixtures(self):
        with self.fake_producer(('failing', 'exit'), update=True) as (fixtures, _):
            with self.assertRaisesRegex(RuntimeError, 'unexpected Maven result'):
                producer.main()
            self.assertTrue(all(path.read_bytes() == b'unchanged fixture' for path in fixtures.iterdir()))

    def test_generator_refuses_missing_or_multiple_fresh_reports(self):
        for condition in ('missing', 'extra'):
            with self.subTest(condition=condition), self.fake_producer(('passing', condition)):
                with self.assertRaisesRegex(RuntimeError, 'reports='):
                    producer.main()

    def test_generator_refuses_changed_evidence_without_refreshing_fixtures(self):
        with self.fake_producer(('failing', 'evidence'), update=True) as (fixtures, _):
            with self.assertRaisesRegex(RuntimeError, 'producer evidence changed'):
                producer.main()
            self.assertTrue(all(path.read_bytes() == b'unchanged fixture' for path in fixtures.iterdir()))

    def test_project_versions_are_verified_before_running(self):
        source = ROOT / 'integrations/surefire/pom.xml'
        producer.verify_project_versions(source)
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / 'pom.xml'
            changed.write_text(source.read_text().replace('<version>3.6.0</version>', '<version>0.0.0</version>'))
            with self.assertRaisesRegex(ValueError, 'versions differ'):
                producer.verify_project_versions(changed)


if __name__ == '__main__':
    unittest.main()
