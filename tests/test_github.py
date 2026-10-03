from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from junit_evidence_gate import Policy, Report, inspect_reports, render_github
from junit_evidence_gate.cli import main
from junit_evidence_gate.core import Issue


ROOT = Path(__file__).resolve().parents[1]


def read_command(line):
    """Read a command boundary and decode once, independently of its renderer."""
    match = re.fullmatch(r"::(error|notice) ([^:\r\n]*)::([^\r\n]*)", line)
    if not match:
        raise AssertionError(f"Not a single annotation command: {line!r}")
    level, raw_properties, raw_message = match.groups()

    def decode(value, property_value=False):
        escapes = {"25": "%", "0D": "\r", "0A": "\n"}
        if property_value:
            escapes.update({"3A": ":", "2C": ","})
        return re.sub(r"%(25|0D|0A|3A|2C)",
                      lambda token: escapes.get(token.group(1), token.group(0)), value)

    properties = {}
    for pair in raw_properties.split(","):
        key, value = pair.split("=", 1)
        if key in properties or key not in {"title", "file"}:
            raise AssertionError("Injected or duplicate annotation property")
        properties[key] = decode(value, True)
    return level, properties, decode(raw_message)


class GitHubFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def xml(self, text, filename="report.xml"):
        path = self.folder / filename
        path.write_text(text, encoding="utf-8")
        return path

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def commands(self, content):
        self.assertTrue(content.endswith("\n"))
        # The runner command boundary is a physical line, not a Unicode separator.
        return [read_command(line) for line in content[:-1].split("\n")]

    def test_accepted_report_has_exact_count_notice(self):
        report = inspect_reports([ROOT / "examples/green.xml"])
        self.assertEqual(render_github(report),
                         "::notice title=JUnit evidence%3A PASS::2 executed; 2 passed; "
                         "0 failed; 0 errors; 1 skipped; 3 unique of 3 records; exit 0\n")

    def test_each_issue_is_an_error_with_its_code_and_xml_source(self):
        path = self.xml('<testsuite name="inflated" tests="39"><testcase name="one"/></testsuite>')
        report = inspect_reports([path])
        commands = self.commands(render_github(report))
        self.assertEqual(commands[0], ("error", {"title": "JUnit evidence: evidence.count_mismatch", "file": str(path)},
                                       "testsuite inflated declares tests=39; observed 1"))
        self.assertEqual(commands[-1][0], "notice")
        self.assertEqual(commands[-1][1], {"title": "JUnit evidence: REJECT"})
        self.assertTrue(commands[-1][2].endswith("exit 1"))

    def test_policy_issue_without_source_has_no_file_property(self):
        report = inspect_reports([ROOT / "examples/green.xml"], Policy(min_executed=3))
        commands = self.commands(render_github(report))
        self.assertEqual(commands[0], ("error", {"title": "JUnit evidence: policy.min_executed"}, "Executed 2; minimum is 3"))

    def test_data_escaping_has_exact_percent_cr_lf_encoding(self):
        report = Report(Policy(), issues=[Issue("evidence.example", "100%\r\n::warning::not another command")])
        self.assertEqual(render_github(report).split("\n")[0],
                         "::error title=JUnit evidence%3A evidence.example::100%25%0D%0A::warning::not another command")
        self.assertEqual(len(self.commands(render_github(report))), 2)

    def test_property_escaping_has_exact_colon_comma_encoding(self):
        source = "C:\\reports\\unit:night,build%25\r\nreport.xml"
        report = Report(Policy(), issues=[Issue("evidence.example", "detail", source)])
        line = render_github(report).split("\n")[0]
        self.assertEqual(line,
                         "::error title=JUnit evidence%3A evidence.example,"
                         "file=C%3A\\reports\\unit%3Anight%2Cbuild%2525%0D%0Areport.xml::detail")
        self.assertEqual(read_command(line)[1]["file"], source)

    def test_encoded_looking_input_is_decoded_only_once(self):
        value = "%0A%0D%25%3A%2C\n::error::payload"
        report = Report(Policy(), issues=[Issue(value, value, value)])
        command = self.commands(render_github(report))[0]
        self.assertEqual(command[1]["title"], "JUnit evidence: " + value)
        self.assertEqual(command[1]["file"], value)
        self.assertEqual(command[2], value)

    def test_message_cannot_inject_another_physical_command(self):
        for payload in ("\n::error::injected", "\r::notice::injected", "\r\n::stop-commands::token",
                        "%0A::add-mask::value", "::notice title=unexpected::value"):
            with self.subTest(payload=payload):
                report = Report(Policy(), issues=[Issue("evidence.example", payload)])
                commands = self.commands(render_github(report))
                self.assertEqual([command[0] for command in commands], ["error", "notice"])
                self.assertEqual(commands[0][2], payload)

    def test_title_cannot_inject_properties_or_a_command(self):
        payload = "bad,file=elsewhere,line=9::payload\n::notice::unexpected"
        report = Report(Policy(), issues=[Issue(payload, "detail")])
        command = self.commands(render_github(report))[0]
        self.assertEqual(command[1], {"title": "JUnit evidence: " + payload})

    def test_path_cannot_inject_properties_or_a_command(self):
        payload = "report.xml,line=9,title=elsewhere::payload\r\n::error::unexpected"
        report = Report(Policy(), issues=[Issue("input.unreadable", "detail", payload)])
        command = self.commands(render_github(report))[0]
        self.assertEqual(command[1], {"title": "JUnit evidence: input.unreadable", "file": payload})

    def test_xml_testcase_and_suite_injection_stays_in_one_issue_message(self):
        path = self.xml(
            '<testsuite name="suite&#10;::notice::suite">'
            '<testcase classname="class&#13;::error::class" name="test%0A&#10;::warning::test"/>'
            '<testcase classname="class&#13;::error::class" name="test%0A&#10;::warning::test"/>'
            '</testsuite>'
        )
        report = inspect_reports([path])
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(len(report.issues), 1)
        issue = report.issues[0]
        self.assertIn("test%0A\n::warning::test", issue.message)
        self.assertIn("suite\n::notice::suite", issue.message)
        commands = self.commands(render_github(report))
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][2], issue.message)

    def test_xml_conflict_name_newline_cannot_add_a_command(self):
        path = self.xml('<testsuite><testcase name="case&#13;&#10;::error::other"><skipped/><failure/></testcase></testsuite>')
        report = inspect_reports([path])
        content = render_github(report)
        commands = self.commands(content)
        self.assertEqual(len(commands), len(report.issues) + 1)
        conflict = next(command for command in commands if command[1]["title"].endswith("evidence.conflicting_status"))
        self.assertIn("case\r\n::error::other", conflict[2])

    def test_unicode_is_preserved_without_becoming_a_physical_command(self):
        value = "한글 👋\u2028::notice::text"
        report = Report(Policy(), issues=[Issue("evidence.example", value, value)])
        command = self.commands(render_github(report))[0]
        self.assertEqual(command[1]["file"], value)
        self.assertEqual(command[2], value)

    def test_renderer_does_not_mutate_the_report(self):
        report = inspect_reports([ROOT / "examples/duplicate.xml"])
        before = report.to_dict()
        first = render_github(report)
        self.assertEqual(report.to_dict(), before)
        self.assertEqual(render_github(report), first)

    def test_source_refers_to_report_without_guessed_testcase_location(self):
        path = self.xml('<testsuite tests="2"><testcase name="one" file="made-up.py" line="99"/></testsuite>')
        command = self.commands(render_github(inspect_reports([path])))[0]
        self.assertEqual(command[1]["file"], str(path))
        self.assertNotIn("line", command[1])
        self.assertNotIn("made-up.py", json.dumps(command))

    def test_failure_stack_trace_and_output_are_not_added_to_annotations(self):
        path = self.xml('<testsuite><testcase name="one"><failure message="private-message">private-trace</failure><system-out>private-output</system-out></testcase></testsuite>')
        content = render_github(inspect_reports([path]))
        self.assertNotIn("private-message", content)
        self.assertNotIn("private-trace", content)
        self.assertNotIn("private-output", content)
        self.assertIn("Recorded 1 failed and 0 error testcases", content)

    def test_cli_github_success_preserves_exit_zero(self):
        code, output, err = self.cli([str(ROOT / "examples/green.xml"), "--format", "github"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(self.commands(output)[0][0], "notice")

    def test_cli_github_rejection_preserves_exit_one(self):
        code, output, err = self.cli([str(ROOT / "examples/contradictory.xml"), "--format", "github"])
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("evidence.count_mismatch", output)
        self.assertTrue(self.commands(output)[-1][2].endswith("exit 1"))

    def test_cli_github_input_error_preserves_priority_exit_two(self):
        code, output, err = self.cli([str(ROOT / "examples/failed.xml"), str(self.folder / "missing.xml"), "--format", "github"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        titles = {command[1]["title"] for command in self.commands(output)}
        self.assertIn("JUnit evidence: input.unmatched", titles)
        self.assertIn("JUnit evidence: policy.failed", titles)
        self.assertTrue(self.commands(output)[-1][2].endswith("exit 2"))

    def test_cli_github_malformed_and_unsafe_xml_remain_input_errors(self):
        for text, code_name in (("<testsuite>", "input.invalid_xml"),
                                ('<!DOCTYPE testsuite><testsuite/>', "input.unsafe_xml")):
            with self.subTest(code=code_name):
                path = self.xml(text)
                code, output, err = self.cli([str(path), "--format", "github"])
                self.assertEqual(code, 2)
                self.assertEqual(err, "")
                self.assertIn(code_name, output)

    def test_cli_output_file_contains_commands_without_printing_them(self):
        output_path = self.folder / "annotations.txt"
        code, output, err = self.cli([str(ROOT / "examples/green.xml"), "--format", "github", "--output", str(output_path)])
        self.assertEqual((code, output, err), (0, "", ""))
        self.assertEqual(self.commands(output_path.read_text(encoding="utf-8"))[0][0], "notice")

    def test_cli_github_keeps_input_overwrite_protection(self):
        original = '<testsuite><testcase name="one"/></testsuite>'
        path = self.xml(original)
        code, output, err = self.cli([str(path), "--format", "github", "--output", str(path)])
        self.assertEqual((code, output), (2, ""))
        self.assertIn("differ", err)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_cli_source_comma_is_an_encoded_property(self):
        path = self.xml('<testsuite tests="2"><testcase name="one"/></testsuite>', "report,night.xml")
        code, output, _ = self.cli([str(path), "--format", "github"])
        self.assertEqual(code, 1)
        self.assertIn("report%2Cnight.xml", output)
        self.assertEqual(self.commands(output)[0][1]["file"], str(path))

    def test_real_cli_uses_utf8_under_cp949_for_xml_names_and_source_paths(self):
        path = self.xml(
            '<testsuite name="suite 👋&#10;::notice::suite">'
            '<testcase name="한글 👋&#13;&#10;::error::forged%0A"/>'
            '<testcase name="한글 👋&#13;&#10;::error::forged%0A"/></testsuite>',
            "emoji-👋,unit.xml",
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp949"
        run = subprocess.run([sys.executable, "-m", "junit_evidence_gate", str(path), "--format", "github"],
                             cwd=ROOT, env=env, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(run.stderr, b"")
        content = run.stdout.decode("utf-8")
        self.assertIn("한글 👋%0D%0A::error::forged%250A", content)
        self.assertIn("emoji-👋%2Cunit.xml", content)
        commands = self.commands(content)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][1]["file"], str(path))
        self.assertEqual(commands[0][2], inspect_reports([path]).issues[0].message)

    def test_real_cli_output_file_remains_utf8_under_cp949(self):
        path = self.xml('<testsuite name="한글 👋" tests="2"><testcase name="one"/></testsuite>', "emoji-👋.xml")
        target = self.folder / "annotations-👋.txt"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp949"
        run = subprocess.run([sys.executable, "-m", "junit_evidence_gate", str(path), "--format", "github", "--output", str(target)],
                             cwd=ROOT, env=env, capture_output=True, timeout=60)
        self.assertEqual((run.returncode, run.stdout, run.stderr), (1, b"", b""))
        content = target.read_text(encoding="utf-8")
        self.assertIn("한글 👋", content)
        self.assertEqual(self.commands(content)[0][1]["file"], str(path))


if __name__ == "__main__":
    unittest.main()
