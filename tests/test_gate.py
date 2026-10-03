from contextlib import redirect_stderr, redirect_stdout
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from junit_evidence_gate import Policy, inspect_reports
from junit_evidence_gate.cli import main, markdown_literal, render_markdown


ROOT = Path(__file__).resolve().parents[1]


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def report(self, text, policy=None, encoding="utf-8"):
        path = self.folder / "report.xml"
        path.write_bytes(text.encode(encoding))
        return inspect_reports([path], policy)

    def codes(self, report):
        return {issue.code for issue in report.issues}

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_green_fixture(self):
        report = inspect_reports([ROOT / "examples/green.xml"])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts, {"records": 3, "unique": 3, "executed": 2, "passed": 2, "failed": 0, "errors": 0, "skipped": 1})

    def test_empty_suite_rejected_even_with_zero_minimum(self):
        report = self.report('<testsuite tests="0"/>', Policy(min_executed=0))
        self.assertEqual(report.exit_code, 1)
        self.assertIn("policy.zero_executed", self.codes(report))

    def test_skipped_only_rejected(self):
        report = inspect_reports([ROOT / "examples/skipped-only.xml"])
        self.assertEqual(report.counts["executed"], 0)
        self.assertIn("policy.zero_executed", self.codes(report))

    def test_declared_total_cannot_replace_records(self):
        report = self.report('<testsuite tests="39"/>')
        self.assertEqual(report.counts["executed"], 0)
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_nested_suite_leaves_counted_once(self):
        report = inspect_reports([ROOT / "examples/nested.xml"])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts["executed"], 3)

    def test_nested_parent_count_checked(self):
        report = self.report('<testsuite tests="2"><testsuite tests="1"><testcase name="a"/></testsuite></testsuite>')
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_testsuites_count_checked(self):
        report = self.report('<testsuites tests="2"><testsuite><testcase name="a"/></testsuite></testsuites>')
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_duplicate_inside_file_is_rejected_and_deduplicated(self):
        report = inspect_reports([ROOT / "examples/duplicate.xml"], Policy(min_executed=2))
        self.assertEqual(report.counts["records"], 2)
        self.assertEqual(report.counts["executed"], 1)
        self.assertTrue({"evidence.duplicate", "policy.min_executed"} <= self.codes(report))

    def test_duplicate_across_files_is_rejected(self):
        a, b = self.folder / "a.xml", self.folder / "b.xml"
        a.write_text('<testsuite name="unit"><testcase classname="c" name="a"/></testsuite>')
        b.write_bytes(a.read_bytes())
        report = inspect_reports([a, b])
        self.assertEqual(report.counts["unique"], 1)
        self.assertIn("evidence.duplicate", self.codes(report))

    def test_repeated_input_file_is_read_once(self):
        fixture = ROOT / "examples/green.xml"
        report = inspect_reports([fixture, fixture, fixture.parent / ".." / "examples" / "green.xml"])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(len(report.files), 1)

    def test_same_name_in_distinct_suites_is_distinct(self):
        report = self.report('<testsuites><testsuite name="a"><testcase name="x"/></testsuite><testsuite name="b"><testcase name="x"/></testsuite></testsuites>')
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts["executed"], 2)

    def test_same_name_in_distinct_classes_is_distinct(self):
        report = self.report('<testsuite><testcase classname="a" name="x"/><testcase classname="b" name="x"/></testsuite>')
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.counts["executed"], 2)

    def test_duplicate_failure_cannot_be_hidden_by_pass(self):
        report = self.report('<testsuite><testcase name="x"/><testcase name="x"><failure/></testcase></testsuite>')
        self.assertEqual(report.counts["failed"], 1)
        self.assertEqual(report.counts["passed"], 0)
        self.assertIn("policy.failed", self.codes(report))

    def test_failure_and_error_counts(self):
        report = inspect_reports([ROOT / "examples/failed.xml"])
        self.assertEqual(report.counts["executed"], 2)
        self.assertEqual(report.counts["failed"], 1)
        self.assertEqual(report.counts["errors"], 1)
        self.assertEqual(report.exit_code, 1)

    def test_multiple_failure_elements_count_one_testcase(self):
        report = self.report('<testsuite tests="1" failures="1"><testcase name="a"><failure/><failure/></testcase></testsuite>')
        self.assertEqual(report.counts["failed"], 1)
        self.assertNotIn("evidence.count_mismatch", self.codes(report))

    def test_testcase_with_error_and_failure_retains_both_declared_counts(self):
        report = self.report('<testsuite tests="1" failures="1" errors="1"><testcase name="a"><failure/><error/></testcase></testsuite>')
        self.assertEqual(report.counts["errors"], 1)
        self.assertNotIn("evidence.count_mismatch", self.codes(report))

    def test_mismatched_failure_count(self):
        report = self.report('<testsuite failures="0"><testcase name="a"><failure/></testcase></testsuite>')
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_mismatched_error_count(self):
        report = self.report('<testsuite errors="0"><testcase name="a"><error/></testcase></testsuite>')
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_mismatched_skip_count(self):
        report = self.report('<testsuite skipped="0"><testcase name="a"><skipped/></testcase></testsuite>')
        self.assertIn("evidence.count_mismatch", self.codes(report))

    def test_invalid_count_formats(self):
        for value in ("-1", "1.0", "one", "", " 1", "1_000", "9" * 13):
            with self.subTest(value=value):
                report = self.report(f'<testsuite tests="{value}"><testcase name="a"/></testsuite>')
                self.assertIn("evidence.invalid_count", self.codes(report))

    def test_absent_counts_are_derived_from_records(self):
        report = self.report('<testsuite><testcase name="a"/></testsuite>')
        self.assertEqual(report.exit_code, 0)

    def test_minimum_boundary(self):
        fixture = ROOT / "examples/green.xml"
        self.assertEqual(inspect_reports([fixture], Policy(min_executed=2)).exit_code, 0)
        self.assertIn("policy.min_executed", self.codes(inspect_reports([fixture], Policy(min_executed=3))))

    def test_skip_budget_boundary(self):
        fixture = ROOT / "examples/green.xml"
        self.assertEqual(inspect_reports([fixture], Policy(max_skipped=1)).exit_code, 0)
        self.assertIn("policy.max_skipped", self.codes(inspect_reports([fixture], Policy(max_skipped=0))))

    def test_skip_ratio_boundary(self):
        fixture = ROOT / "examples/green.xml"
        self.assertEqual(inspect_reports([fixture], Policy(max_skip_ratio=1/3)).exit_code, 0)
        self.assertIn("policy.skip_ratio", self.codes(inspect_reports([fixture], Policy(max_skip_ratio=0.3))))

    def test_invalid_policy_values(self):
        for kwargs in ({"min_executed": -1}, {"max_skipped": -1}, {"max_skip_ratio": 1.1}, {"max_skip_ratio": float("nan")}, {"max_skip_ratio": True}, {"max_skip_ratio": "0.5"}, {"max_bytes": 0}, {"max_nodes": 0}, {"max_depth": 0}, {"min_executed": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Policy(**kwargs)

    def test_status_attributes_are_respected(self):
        report = self.report('<testsuite><testcase name="a" status="disabled"/><testcase name="b" status="failed"/><testcase name="c" status="error"/></testsuite>')
        self.assertEqual((report.counts["skipped"], report.counts["failed"], report.counts["errors"]), (1, 1, 1))

    def test_unknown_status_is_rejected(self):
        report = self.report('<testsuite><testcase name="a" status="todo"/></testsuite>')
        self.assertIn("evidence.unknown_status", self.codes(report))
        self.assertEqual(report.counts["executed"], 0)

    def test_skipped_failure_conflict_is_rejected(self):
        report = self.report('<testsuite><testcase name="a"><skipped/><failure/></testcase></testsuite>')
        self.assertIn("evidence.conflicting_status", self.codes(report))

    def test_attribute_result_conflict_is_rejected(self):
        report = self.report('<testsuite><testcase name="a" status="passed"><failure/></testcase></testsuite>')
        self.assertIn("evidence.conflicting_status", self.codes(report))

    def test_run_attribute_allows_failure_result(self):
        report = self.report('<testsuite><testcase name="a" status="run"><failure/></testcase></testsuite>')
        self.assertNotIn("evidence.conflicting_status", self.codes(report))
        self.assertIn("policy.failed", self.codes(report))

    def test_missing_testcase_name_rejected(self):
        for attr in ("", 'name=""', 'name="  "'):
            with self.subTest(attr=attr):
                report = self.report(f'<testsuite><testcase {attr}/></testsuite>')
                self.assertIn("evidence.missing_name", self.codes(report))

    def test_namespaced_xml(self):
        report = self.report('<testsuite xmlns="urn:example" tests="1"><testcase name="a"/></testsuite>')
        self.assertEqual(report.exit_code, 0)

    def test_metadata_is_not_counted_as_testcase_evidence(self):
        report = self.report('<testsuite><properties><testcase name="hidden"/></properties></testsuite>')
        self.assertEqual(report.counts["executed"], 0)
        self.assertIn("evidence.structure", self.codes(report))

    def test_direct_testcase_under_testsuites_is_rejected(self):
        report = self.report('<testsuites><testcase name="a"/></testsuites>')
        self.assertIn("evidence.structure", self.codes(report))

    def test_nested_testcase_is_rejected(self):
        report = self.report('<testsuite><testcase name="a"><testcase name="b"/></testcase></testsuite>')
        self.assertIn("evidence.structure", self.codes(report))
        self.assertEqual(report.counts["records"], 1)

    def test_nested_testcase_inside_case_metadata_is_rejected(self):
        report = self.report('<testsuite><testcase name="a"><properties><testcase name="hidden"/></properties></testcase></testsuite>')
        self.assertIn("evidence.structure", self.codes(report))
        self.assertEqual(report.counts["records"], 1)

    def test_retry_extensions_cannot_silently_pass(self):
        for tag in ("rerunFailure", "flakyFailure", "customResult"):
            with self.subTest(tag=tag):
                report = self.report(f'<testsuite><testcase name="a"><{tag}/></testcase></testsuite>')
                self.assertIn("evidence.unsupported_result", self.codes(report))
                self.assertEqual(report.exit_code, 1)

    def test_wrong_root_is_input_error(self):
        report = self.report('<report><testsuite><testcase name="a"/></testsuite></report>')
        self.assertEqual(report.exit_code, 2)
        self.assertIn("input.root", self.codes(report))

    def test_malformed_xml_is_input_error(self):
        report = self.report('<testsuite>')
        self.assertEqual(report.exit_code, 2)
        self.assertIn("input.invalid_xml", self.codes(report))

    def test_missing_file_is_input_error(self):
        report = inspect_reports([self.folder / "missing.xml"])
        self.assertEqual(report.exit_code, 2)
        self.assertIn("input.unreadable", self.codes(report))

    def test_directory_is_input_error(self):
        self.assertEqual(inspect_reports([self.folder]).exit_code, 2)

    def test_no_paths_is_input_error(self):
        self.assertEqual(inspect_reports([]).exit_code, 2)

    def test_dtd_rejected_in_utf8_and_utf16(self):
        text = '<?xml version="1.0"?><!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="&x;"/></testsuite>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                report = self.report(text, encoding=encoding)
                self.assertEqual(report.exit_code, 2)
                self.assertIn("input.unsafe_xml", self.codes(report))

    def test_external_entity_rejected(self):
        text = '<!DOCTYPE testsuite SYSTEM "file:///unreadable-secret"><testsuite><testcase name="a"/></testsuite>'
        report = self.report(text)
        self.assertIn("input.unsafe_xml", self.codes(report))

    def test_predefined_xml_entities_supported(self):
        report = self.report('<testsuite><testcase name="&lt;a&gt;&amp;&quot;&apos;"/></testsuite>')
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.cases[0].name, '<a>&"\'')

    def test_byte_limit(self):
        text = '<testsuite><testcase name="a"/></testsuite>'
        self.assertEqual(self.report(text, Policy(max_bytes=len(text))).exit_code, 0)
        self.assertIn("input.unsafe_xml", self.codes(self.report(text, Policy(max_bytes=len(text)-1))))

    def test_depth_limit(self):
        text = '<testsuite><testsuite><testcase name="a"/></testsuite></testsuite>'
        self.assertEqual(self.report(text, Policy(max_depth=3)).exit_code, 0)
        self.assertIn("input.unsafe_xml", self.codes(self.report(text, Policy(max_depth=2))))

    def test_node_limit(self):
        text = '<testsuite><testcase name="a"/></testsuite>'
        self.assertEqual(self.report(text, Policy(max_nodes=2)).exit_code, 0)
        self.assertIn("input.unsafe_xml", self.codes(self.report(text, Policy(max_nodes=1))))

    def test_json_preserves_literal_names(self):
        report = inspect_reports([ROOT / "examples/literal-names.xml"])
        decoded = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(decoded["cases"][0]["name"], report.cases[0].name)

    def test_markdown_cells_roundtrip_without_formatting(self):
        values = ["**bold** _em_ ~~gone~~", "[link](https://x) ![img](x)", '<script>&" | `code` \\', "first\nsecond", "한글 👋"]
        for value in values:
            with self.subTest(value=value):
                escaped = markdown_literal(value)
                self.assertEqual(html.unescape(escaped.replace("<br>", "\n")), value)
                self.assertNotIn("|", escaped)
                self.assertNotIn("`", escaped)
                self.assertNotIn("*", escaped)
                self.assertNotIn("<script>", escaped)

    def test_markdown_controls_are_visible(self):
        self.assertEqual(markdown_literal("a\t\x1b"), "a&#92;u0009&#92;u001b")

    def test_markdown_report_has_fixed_columns(self):
        report = inspect_reports([ROOT / "examples/literal-names.xml"])
        output = render_markdown(report)
        row = output.splitlines()[-1]
        self.assertEqual(row.count("|"), 5)
        self.assertIn("<br>", row)
        self.assertNotIn("<img>", row)

    def test_cli_module_real_subprocess(self):
        run = subprocess.run([sys.executable, "-m", "junit_evidence_gate", str(ROOT / "examples/green.xml")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["counts"]["executed"], 2)

    def test_cli_rejection_exit_code(self):
        code, output, err = self.cli([str(ROOT / "examples/skipped-only.xml")])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["accepted"])
        self.assertEqual(err, "")

    def test_cli_unmatched_pattern_exit_code(self):
        code, output, _ = self.cli([str(self.folder / "*.xml")])
        self.assertEqual(code, 2)
        self.assertIn("input.unmatched", {issue["code"] for issue in json.loads(output)["issues"]})

    def test_cli_glob_and_policy(self):
        (self.folder / "one.xml").write_text('<testsuite name="one"><testcase name="a"/></testsuite>')
        (self.folder / "two.xml").write_text('<testsuite name="two"><testcase name="b"/></testsuite>')
        code, output, _ = self.cli([str(self.folder / "*.xml"), "--min-executed", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["counts"]["executed"], 2)

    def test_cli_partial_match_still_rejects(self):
        code, output, _ = self.cli([str(ROOT / "examples/green.xml"), str(self.folder / "missing.xml")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["counts"]["executed"], 2)

    def test_cli_output_file(self):
        path = self.folder / "summary.md"
        code, output, _ = self.cli([str(ROOT / "examples/literal-names.xml"), "--format", "markdown", "--output", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        self.assertIn("# JUnit evidence: PASS", path.read_text(encoding="utf-8"))

    def test_cli_cannot_overwrite_input(self):
        path = self.folder / "report.xml"
        original = '<testsuite><testcase name="a"/></testsuite>'
        path.write_text(original)
        code, _, err = self.cli([str(path), "--output", str(path)])
        self.assertEqual(code, 2)
        self.assertEqual(path.read_text(), original)
        self.assertIn("differ", err)

    def test_cli_cannot_overwrite_hardlink_to_input(self):
        path, alias = self.folder / "report.xml", self.folder / "alias.xml"
        original = '<testsuite><testcase name="a"/></testsuite>'
        path.write_text(original)
        try:
            os.link(path, alias)
        except OSError as exc:
            self.skipTest(f"Hard links unavailable: {exc}")
        code, _, err = self.cli([str(path), "--output", str(alias)])
        self.assertEqual(code, 2)
        self.assertEqual(path.read_text(), original)
        self.assertIn("differ", err)

    def test_cli_unwritable_output_is_code_two(self):
        code, _, err = self.cli([str(ROOT / "examples/green.xml"), "--output", str(self.folder)])
        self.assertEqual(code, 2)
        self.assertIn("cannot be written", err)

    def test_cli_invalid_policy_is_code_two(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as captured:
            main([str(ROOT / "examples/green.xml"), "--max-skip-ratio", "1.2"])
        self.assertEqual(captured.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
