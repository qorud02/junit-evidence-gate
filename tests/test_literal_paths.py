"""Literal report paths take priority over wildcard interpretation."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from junit_evidence_gate.cli import main


class LiteralPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def write_report(self, name, failed=False):
        path = self.folder / name
        outcome = "<failure/>" if failed else ""
        path.write_text(
            f'<testsuite name="{name}"><testcase name="case">{outcome}</testcase></testsuite>',
            encoding="utf-8",
        )
        return path

    def cli(self, paths):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([str(path) for path in paths])
        return code, json.loads(output.getvalue())

    def test_existing_bracketed_filename_is_read_without_matches(self):
        path = self.write_report("unit[linux].xml")
        code, report = self.cli([path])
        self.assertEqual(code, 0)
        self.assertEqual(report["files"], [str(path)])
        self.assertEqual(report["counts"]["passed"], 1)

    def test_similar_glob_match_cannot_replace_literal_failing_report(self):
        literal = self.write_report("unit[linux].xml", failed=True)
        decoy = self.write_report("unitl.xml")
        before = {path: path.read_bytes() for path in (literal, decoy)}
        code, report = self.cli([literal])
        self.assertEqual(code, 1)
        self.assertEqual(report["files"], [str(literal)])
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["counts"]["passed"], 0)
        self.assertIn("policy.failed", {issue["code"] for issue in report["issues"]})
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_literal_and_pattern_keep_input_order_and_deduplicate(self):
        literal = self.write_report("unit[linux].xml", failed=True)
        decoy = self.write_report("unitl.xml")
        code, report = self.cli([literal, self.folder / "unit*.xml"])
        self.assertEqual(code, 1)
        self.assertEqual(report["files"], [str(literal), str(decoy)])
        self.assertEqual(report["counts"]["records"], 2)
        self.assertEqual(report["counts"]["failed"], 1)

    def test_nonexistent_literal_still_expands_a_pattern(self):
        first = self.write_report("unit-a.xml")
        second = self.write_report("unit-b.xml")
        code, report = self.cli([self.folder / "unit*.xml"])
        self.assertEqual(code, 0)
        self.assertEqual(report["files"], [str(first), str(second)])
        self.assertEqual(report["counts"]["executed"], 2)

    def test_unmatched_pattern_still_rejects_partial_input(self):
        valid = self.write_report("valid.xml")
        pattern = self.folder / "missing[ab].xml"
        code, report = self.cli([valid, pattern])
        self.assertEqual(code, 2)
        self.assertEqual(report["files"], [str(valid)])
        self.assertIn("input.unmatched", {issue["code"] for issue in report["issues"]})

    def test_literal_star_and_question_mark_on_supported_filesystems(self):
        for name in ("unit*.xml", "unit?.xml"):
            with self.subTest(name=name):
                try:
                    literal = self.write_report(name, failed=True)
                except OSError:
                    self.skipTest("This filesystem does not support star/question-mark filenames")
                self.write_report("unitx.xml")
                code, report = self.cli([literal])
                self.assertEqual(code, 1)
                self.assertEqual(report["files"], [str(literal)])
                self.assertEqual(report["counts"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
