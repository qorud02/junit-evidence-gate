"""Real filesystem regressions for untrusted input and output paths."""

from contextlib import redirect_stderr, redirect_stdout
import errno
import glob
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_cyclic_literal_input_returns_unreadable_issue(self):
        path = self.loop("loop.xml")
        code, output, errors = self.cli(path)
        self.assertEqual((code, errors), (2, ""))
        self.assertIn("input.unreadable", {issue["code"] for issue in json.loads(output)["issues"]})

    def assert_unreadable_literal(self, path, *, files=None):
        code, output, errors = self.cli(path)
        report = json.loads(output)
        self.assertEqual((code, errors), (2, ""))
        self.assertFalse(report["accepted"])
        self.assertEqual(report["counts"]["executed"], 0)
        self.assertIn(
            ("input.unreadable", str(path)),
            {(issue["code"], issue["source"]) for issue in report["issues"]},
        )
        self.assertNotIn("input.unmatched", {issue["code"] for issue in report["issues"]})
        if files is not None:
            self.assertEqual(report["files"], files)

    def test_broken_bracketed_literal_cannot_select_passing_glob_match(self):
        decoy = self.report_file("o.xml")
        original = decoy.read_bytes()
        path = self.folder / "[ok].xml"
        try:
            path.symlink_to("missing.xml")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Broken symlinks unavailable: {exc}")
        self.assert_unreadable_literal(path, files=[str(path)])
        self.assertEqual(decoy.read_bytes(), original)
        self.assertTrue(path.is_symlink())

    def test_cyclic_bracketed_literal_cannot_select_passing_glob_match(self):
        decoy = self.report_file("o.xml")
        path = self.loop("[ok].xml")
        self.assert_unreadable_literal(path)
        self.assertTrue(decoy.exists())
        self.assertTrue(path.is_symlink())

    def test_cyclic_bracketed_parent_cannot_select_passing_glob_match(self):
        (self.folder / "o").mkdir()
        self.report_file("o/green.xml")
        parent = self.loop("[ok]", directory=True)
        self.assert_unreadable_literal(parent / "green.xml")
        self.assertTrue(parent.is_symlink())

    def test_broken_bracketed_parent_cannot_select_passing_glob_match(self):
        (self.folder / "o").mkdir()
        self.report_file("o/green.xml")
        parent = self.folder / "[ok]"
        try:
            parent.symlink_to("missing-dir", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Broken directory symlinks unavailable: {exc}")
        self.assert_unreadable_literal(parent / "green.xml")
        self.assertTrue(parent.is_symlink())

    def test_glob_through_readable_symlink_parent_still_matches(self):
        (self.folder / "o").mkdir()
        self.report_file("o/o.xml")
        parent = self.folder / "reports"
        try:
            parent.symlink_to("o", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")
        for pattern in ("[ok].xml", "*.xml"):
            with self.subTest(pattern=pattern):
                code, output, errors = self.cli(parent / pattern)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(json.loads(output)["files"], [str(parent / "o.xml")])

    def test_missing_bracketed_parent_still_expands_glob(self):
        (self.folder / "o").mkdir()
        report = self.report_file("o/green.xml")
        code, output, errors = self.cli(self.folder / "[ok]/green.xml")
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output)["files"], [str(report)])

    def test_lstat_read_errors_cannot_select_passing_glob_match(self):
        self.report_file("o.xml")
        path = self.folder / "[ok].xml"
        original_lstat = Path.lstat
        windows_cycle = OSError(errno.EINVAL, "Cannot resolve filename")
        windows_cycle.winerror = 1921
        windows_reparse = OSError(errno.EINVAL, "Cannot access file")
        windows_reparse.winerror = 1920
        errors = [
            PermissionError(errno.EACCES, "Access denied"),
            OSError(errno.ELOOP, "Symlink loop"),
            OSError(errno.EIO, "I/O error"),
            windows_cycle,
            windows_reparse,
        ]
        for error in errors:
            with self.subTest(errno=error.errno, winerror=getattr(error, "winerror", None)):
                def fail_literal(candidate):
                    if candidate == path:
                        raise error
                    return original_lstat(candidate)

                with patch.object(Path, "lstat", autospec=True, side_effect=fail_literal) as probe:
                    with patch("junit_evidence_gate.cli.glob.glob", wraps=glob.glob) as expand:
                        self.assert_unreadable_literal(path, files=[str(path)])
                probe.assert_any_call(path)
                expand.assert_not_called()

    def test_unreadable_literal_ancestor_cannot_select_passing_glob_match(self):
        (self.folder / "o").mkdir()
        self.report_file("o/green.xml")
        parent = self.folder / "[ok]"
        path = parent / "green.xml"
        original_lstat = Path.lstat

        def fail_parent(candidate):
            if candidate == parent:
                raise PermissionError(errno.EACCES, "Access denied")
            return original_lstat(candidate)

        with patch.object(Path, "lstat", autospec=True, side_effect=fail_parent):
            with patch("junit_evidence_gate.cli.glob.glob", wraps=glob.glob) as expand:
                self.assert_unreadable_literal(path)
        expand.assert_not_called()

    def test_missing_overlong_and_invalid_lstat_errors_still_expand_glob(self):
        decoy = self.report_file("o.xml")
        path = self.folder / "[ok].xml"
        original_lstat = Path.lstat
        errors = [OSError(code, "Not a literal path") for code in
                  (errno.ENOENT, errno.ENOTDIR, errno.ENAMETOOLONG, errno.EINVAL)]
        for code, winerror in ((errno.EINVAL, 123), (errno.ENOENT, 206), (errno.ENOTDIR, 267)):
            error = OSError(code, "Not a Windows literal path")
            error.winerror = winerror
            errors.append(error)
        errors.append(ValueError("Invalid path"))
        for error in errors:
            with self.subTest(error=repr(error), winerror=getattr(error, "winerror", None)):
                def fail_literal(candidate):
                    if candidate == path:
                        raise error
                    return original_lstat(candidate)

                with patch.object(Path, "lstat", autospec=True, side_effect=fail_literal) as probe:
                    code, output, stderr = self.cli(path)
                self.assertEqual((code, stderr), (0, ""))
                report = json.loads(output)
                self.assertTrue(report["accepted"])
                self.assertEqual(report["files"], [str(decoy)])
                probe.assert_any_call(path)

    def test_invalid_nul_pattern_returns_unmatched_rejection(self):
        code, output, errors = self.cli(str(self.folder / "invalid") + "\0*.xml")
        self.assertEqual((code, errors), (2, ""))
        report = json.loads(output)
        self.assertFalse(report["accepted"])
        self.assertIn("input.unmatched", {issue["code"] for issue in report["issues"]})

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

    def test_glob_exceeding_windows_path_limit_still_matches(self):
        report = self.report_file("a.xml")
        pattern = self.folder / ("[" + "a" * 32768 + "].xml")
        code, output, errors = self.cli(pattern)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output)["files"], [str(report)])

    def test_overlong_glob_cannot_bypass_unreadable_literal_parent(self):
        (self.folder / "o").mkdir()
        self.report_file("o/a.xml")
        parent = self.folder / "[ok]"
        for target in (parent.name, "missing-dir"):
            try:
                parent.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Directory symlinks unavailable: {exc}")
            with self.subTest(target=target):
                pattern = parent / ("[" + "a" * 32768 + "].xml")
                self.assert_unreadable_literal(pattern)
            parent.unlink()

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
