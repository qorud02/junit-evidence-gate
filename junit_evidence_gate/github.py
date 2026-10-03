"""Render report issues as GitHub Actions workflow-command annotations."""

from __future__ import annotations

from .core import Report


def _escape_data(value: str) -> str:
    # Match actions/toolkit's command data encoding, with percent escaped first.
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    # Colons and commas also delimit workflow-command properties.
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def _annotation(level: str, title: str, message: str, source: str = "") -> str:
    properties = ["title=" + _escape_property(title)]
    if source:
        properties.append("file=" + _escape_property(source))
    return f"::{level} {','.join(properties)}::{_escape_data(message)}"


def render_github(report: Report) -> str:
    """Return one error per issue and a count notice, with a final newline.

    Commands create annotations when written to a GitHub Actions step log.
    File properties refer to source XML reports; no testcase locations are guessed.
    Rendering does not change the report, its policy, or its exit code.
    """
    lines = [
        _annotation("error", f"JUnit evidence: {issue.code}", issue.message, issue.source)
        for issue in report.issues
    ]
    counts = report.counts
    verdict = "PASS" if report.exit_code == 0 else "REJECT"
    summary = (
        f"{counts['executed']} executed; {counts['passed']} passed; "
        f"{counts['failed']} failed; {counts['errors']} errors; "
        f"{counts['skipped']} skipped; {counts['unique']} unique of {counts['records']} records; "
        f"exit {report.exit_code}"
    )
    lines.append(_annotation("notice", f"JUnit evidence: {verdict}", summary))
    return "\n".join(lines) + "\n"
