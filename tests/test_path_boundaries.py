"""Real filesystem regressions for untrusted input and output paths."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from junit_evidence_gate import inspect_reports
from junit_evidence_gate.cli import main


class PathBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def report_file(self, name="green.xml"):
        path = self.folder / name
        path.write_text('<testsuite><testcase name="one"/></testsuite>', encoding="utf-8")
        return path

    def loop(self, name, *, directory=False):
        path = self.folder / name
        try:
            path.symlink_to(path.name, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Cyclic symlinks unavailable: {exc}")
        return path

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main([str(arg) for arg in args])
        return code, output.getvalue(), errors.getvalue()

    def test_cyclic_input_returns_unreadable_issue(self):
        path = self.loop("loop.xml")
        report = inspect_reports([path])
        self.assertEqual(report.exit_code, 2)
        self.assertIn("input.unreadable", {issue.code for issue in report.issues})

    def test_cyclic_glob_match_returns_json_rejection(self):
        path = self.loop("loop.xml")
        code, output, errors = self.cli(self.folder / "*.xml")
        self.assertEqual((code, errors), (2, ""))
        self.assertIn("input.unreadable", {issue["code"] for issue in json.loads(output)["issues"]})
        self.assertTrue(path.is_symlink())

    def test_cyclic_output_is_rejected_without_changing_input(self):
        report = self.report_file()
        original = report.read_bytes()
        target = self.loop("summary.json")
        code, output, errors = self.cli(report, "--output", target)
        self.assertEqual((code, output), (2, ""))
        self.assertIn("cannot be written", errors)
        self.assertEqual(report.read_bytes(), original)
        self.assertTrue(target.is_symlink())

    def test_cyclic_output_parent_is_rejected_without_changing_input(self):
        report = self.report_file()
        original = report.read_bytes()
        parent = self.loop("output-dir", directory=True)
        code, output, errors = self.cli(report, "--output", parent / "summary.json")
        self.assertEqual((code, output), (2, ""))
        self.assertIn("cannot be written", errors)
        self.assertEqual(report.read_bytes(), original)

    def test_overlong_valid_glob_still_finds_readable_report(self):
        report = self.report_file("a" * 70 + ".xml")
        pattern = self.folder / ("[ab]" * 70 + ".xml")
        code, output, errors = self.cli(pattern)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output)["files"], [str(report)])

    def test_overlong_unmatched_glob_is_reported(self):
        pattern = self.folder / ("[ab]" * 70 + ".xml")
        code, output, errors = self.cli(pattern)
        self.assertEqual((code, errors), (2, ""))
        self.assertIn("input.unmatched", {issue["code"] for issue in json.loads(output)["issues"]})

    def test_overlong_unmatched_literal_is_reported(self):
        literal = self.folder / ("z" * 300 + ".xml")
        code, output, errors = self.cli(literal)
        self.assertEqual((code, errors), (2, ""))
        self.assertIn("input.unmatched", {issue["code"] for issue in json.loads(output)["issues"]})
