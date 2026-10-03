# Summary output encoding

Markdown summaries use UTF-8 on stdout, including when `PYTHONIOENCODING` selects a legacy Windows code page. Unicode suite names, testcase names and XML paths retain their text, and report verdicts keep the exit codes `0`, `1` and `2`.

```sh
junit-evidence-gate reports.xml --format markdown
junit-evidence-gate reports.xml --format markdown --output summary.md
```

`--output` saves a UTF-8 file. JSON retains ASCII escapes that a JSON reader decodes to the original names. GitHub output uses UTF-8 workflow commands with escaped command properties and line breaks.

Python callers using `main()` keep their text stream's encoding and error settings after a Markdown write. Custom text captures such as `StringIO` receive the summary string directly. A capture that cannot encode the text returns exit code `2`.
