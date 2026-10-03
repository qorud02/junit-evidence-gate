"""Real legacy-encoding subprocesses and embedded output stream contracts."""
import contextlib
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from junit_evidence_gate.cli import main

ROOT = Path(__file__).resolve().parents[1]


class OutputEncodingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.inputs = []
        for label, payload, expected in (
            ("green", '<testsuite name="suite 👋" tests="1"><testcase classname="class|👋" name="한글 👋&#10;second"/></testsuite>', 0),
            ("contradictory", '<testsuite name="suite 👋" tests="2"><testcase classname="class|👋" name="한글 👋&#10;second"/></testsuite>', 1),
            ("malformed", '<testsuite>', 2),
        ):
            path = self.folder / (label + "-👋.xml")
            path.write_text(payload, encoding="utf-8")
            self.inputs.append((path, expected))

    def subprocess_output(self, path, format_name, expected):
        env = dict(os.environ, PYTHONIOENCODING="cp949")
        arguments = [sys.executable, "-m", "junit_evidence_gate", str(path), "--format", format_name]
        output = self.folder / (path.stem + "." + format_name + ".txt")
        saved = subprocess.run(arguments + ["--output", str(output)], cwd=ROOT, env=env,
                               capture_output=True, timeout=60)
        redirected = subprocess.run(arguments, cwd=ROOT, env=env, capture_output=True, timeout=60)
        self.assertEqual((saved.returncode, saved.stdout, saved.stderr), (expected, b"", b""))
        self.assertEqual((redirected.returncode, redirected.stderr), (expected, b""))
        self.assertEqual(redirected.stdout, output.read_bytes())
        return redirected.stdout.decode("utf-8")

    def test_json_stdout_preserves_ascii_escapes_and_verdict_under_cp949(self):
        for path, expected in self.inputs:
            with self.subTest(exit_code=expected):
                content = self.subprocess_output(path, "json", expected)
                self.assertTrue(content.isascii())
                report = json.loads(content)
                self.assertEqual(report["exit_code"], expected)
                self.assertEqual(report["files"], [str(path)])
                if expected != 2:
                    self.assertEqual(report["cases"][0]["suite"], ["suite 👋"])
                    self.assertEqual(report["cases"][0]["classname"], "class|👋")
                    self.assertEqual(report["cases"][0]["name"], "한글 👋\nsecond")
                    self.assertEqual(report["cases"][0]["source"], str(path))

    def test_markdown_stdout_is_utf8_and_matches_saved_file_under_cp949(self):
        for path, expected in self.inputs:
            with self.subTest(exit_code=expected):
                content = self.subprocess_output(path, "markdown", expected)
                if expected != 2:
                    row = content.splitlines()[-1]
                    self.assertEqual(row.count("|"), 5)
                    cells = [html.unescape(cell.strip().replace("<br>", "\n")) for cell in row.split("|")[1:-1]]
                    self.assertEqual(cells, ["suite 👋", "class|👋", "한글 👋\nsecond", "passed"])
                if expected:
                    source_row = next(line for line in content.splitlines()
                                      if "evidence&#46;count&#95;mismatch" in line or "input&#46;invalid&#95;xml" in line)
                    self.assertEqual(html.unescape(source_row.split("|")[-2].strip()), str(path))

    def test_embedded_markdown_write_restores_stream_encoding_and_errors(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp949", errors="strict")
        self.addCleanup(stream.close)
        before = (stream.encoding, stream.errors, stream.line_buffering, stream.write_through)
        with contextlib.redirect_stdout(stream):
            result = main([str(self.inputs[0][0]), "--format", "markdown"])
        self.assertEqual(result, 0)
        self.assertEqual((stream.encoding, stream.errors, stream.line_buffering, stream.write_through), before)
        stream.flush()
        self.assertIn("한글 👋<br>second", buffer.getvalue().decode("utf-8"))

    def test_custom_legacy_capture_returns_output_error_without_reconfiguration(self):
        class LegacyCapture:
            def write(self, content):
                return len(content.encode("cp949"))

        with contextlib.redirect_stdout(LegacyCapture()):
            result = main([str(self.inputs[0][0]), "--format", "markdown"])
        self.assertEqual(result, 2)

    def test_read_write_capture_after_read_keeps_supported_writes_and_settings(self):
        for encoding, path, expected in (
            ("cp949", ROOT / "examples/green.xml", 0),
            ("utf-8", self.inputs[0][0], 0),
            ("cp949", self.inputs[0][0], 2),
        ):
            with self.subTest(encoding=encoding, exit_code=expected):
                buffer = io.BytesIO(b"prefix\n")
                stream = io.TextIOWrapper(buffer, encoding=encoding, errors="strict")
                self.addCleanup(stream.close)
                self.assertEqual(stream.readline(), "prefix\n")
                before = (stream.encoding, stream.errors, stream.line_buffering, stream.write_through)
                errors = io.StringIO()
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(errors):
                    result = main([str(path), "--format", "markdown"])
                self.assertEqual(result, expected)
                self.assertEqual(errors.getvalue(), "")
                self.assertEqual((stream.encoding, stream.errors, stream.line_buffering, stream.write_through), before)
                stream.flush()
                if expected == 0:
                    self.assertIn("# JUnit evidence: PASS", buffer.getvalue().decode(encoding))
                else:
                    self.assertEqual(buffer.getvalue(), b"prefix\n")


if __name__ == "__main__":
    unittest.main()
