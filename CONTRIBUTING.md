# Contributing

Start with a small XML fixture that reproduces a real report convention or policy defect. Include the producer and version when possible, and remove secrets and private test output.

Run these checks from the repository root:

```sh
python -m unittest discover -s tests -v
python examples/run_demo.py
python -m pip install .
junit-evidence-gate examples/green.xml --min-executed 2 --max-skipped 1
```

New parser support should include a passing report and a contradictory report, expected counts, expected issue codes, and tests for the CLI exit status. Keep public JSON field names and issue codes stable within schema version 1.

Useful next contributions include real producer fixtures for Maven Surefire and Jest, a documented policy for retry formats, and a configurable identity selector with explicit duplicate semantics. Preserve the byte, depth, and node limits when changing XML handling.

The runtime uses Python's standard library. Build tooling uses setuptools. Keep optional integrations separate from the core gate.
