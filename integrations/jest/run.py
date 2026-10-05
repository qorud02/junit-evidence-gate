"""Generate real Jest reports and compare their sanitized evidence with fixtures."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURES = ROOT / 'examples/producers/jest'
SCENARIOS = {
    'passing': (0, 0, {'records': 3, 'unique': 3, 'executed': 2, 'passed': 2,
                       'failed': 0, 'errors': 0, 'skipped': 1}, set()),
    'failing': (1, 1, {'records': 2, 'unique': 2, 'executed': 2, 'passed': 1,
                       'failed': 1, 'errors': 0, 'skipped': 0}, {'policy.failed'}),
    'todo': (0, 1, {'records': 2, 'unique': 2, 'executed': 2, 'passed': 2,
                    'failed': 0, 'errors': 0, 'skipped': 0}, {'evidence.count_mismatch'}),
    'suite-error': (1, 1, {'records': 2, 'unique': 1, 'executed': 1, 'passed': 0,
                           'failed': 0, 'errors': 1, 'skipped': 0},
                    {'evidence.count_mismatch', 'evidence.duplicate', 'policy.failed'}),
}


def gate(path):
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE'}}
    result = subprocess.run([sys.executable, '-m', 'junit_evidence_gate', str(path)],
                            cwd=ROOT, env=environment, capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'Gate could not read producer report: {result.stderr}')
    report = json.loads(result.stdout)
    if report['schema_version'] != 1 or report['exit_code'] != result.returncode:
        raise RuntimeError('Unexpected gate output contract')
    return {'exit_code': result.returncode, 'counts': report['counts'],
            'issue_codes': sorted({issue['code'] for issue in report['issues']})}


def normalized(data):
    root = ET.fromstring(data)
    for node in root.iter():
        for attribute in ('time', 'timestamp', 'hostname'):
            node.attrib.pop(attribute, None)
        for attribute, value in list(node.attrib.items()):
            for prefix in (str(HERE), HERE.as_posix()):
                value = value.replace(prefix, 'jest-fixtures')
            if 'jest-fixtures' in value or value.startswith(('cases\\', 'cases/')):
                value = value.replace('\\', '/')
            node.set(attribute, value)
        if node.tag in {'failure', 'error', 'system-out', 'system-err'}:
            node.text = None
    ET.indent(root, space='  ')
    result = ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
    if str(ROOT).encode() in result or ROOT.as_posix().encode() in result:
        raise RuntimeError('Normalized fixture still contains the checkout path')
    return result


def check_fixture(name, data, update):
    fixture = FIXTURES / (name + '.xml')
    if update:
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(data)
    elif not fixture.is_file() or fixture.read_bytes() != data:
        raise RuntimeError(f'{fixture.name} differs; review producer output before --update-fixtures')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update-fixtures', action='store_true', help='Replace committed sanitized fixtures after checking real reports')
    parser.add_argument('--keep-reports', type=Path, help='Keep raw XML and runner logs in this directory')
    args = parser.parse_args()
    node = shutil.which('node')
    executable = HERE / 'node_modules/jest/bin/jest.js'
    if node is None or not executable.is_file():
        parser.error('Install Node 24 and run npm ci --ignore-scripts in integrations/jest first')
    expected = json.loads((HERE / 'package.json').read_text())['devDependencies']
    installed = {name: json.loads((HERE / 'node_modules' / name / 'package.json').read_text())['version']
                 for name in expected}
    if installed != expected:
        raise RuntimeError(f'Installed producer versions differ: {installed}')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('JEST_') and key not in {'NODE_OPTIONS', 'NODE_PATH'}}
    environment.update(CI='true', FORCE_COLOR='0')
    summaries = []
    generated = {}
    with tempfile.TemporaryDirectory(prefix='junit-jest-producer-') as temporary:
        folder = Path(temporary)
        for name, (runner_exit, gate_exit, counts, codes) in SCENARIOS.items():
            raw = folder / (name + '.xml')
            process = subprocess.run(
                [node, str(executable), '--ci', '--runInBand', '--no-cache', '--no-color',
                 '--runTestsByPath', 'cases/' + name + '.test.js'],
                cwd=HERE, env=dict(environment, JEST_JUNIT_OUTPUT_FILE=str(raw)),
                capture_output=True, text=True, encoding='utf-8', timeout=120,
            )
            if args.keep_reports:
                args.keep_reports.mkdir(parents=True, exist_ok=True)
                (args.keep_reports / (name + '.log')).write_text(process.stdout + process.stderr, encoding='utf-8')
                if raw.is_file():
                    shutil.copyfile(raw, args.keep_reports / raw.name)
            if process.returncode != runner_exit or not raw.is_file():
                raise RuntimeError(f'{name}: unexpected Jest result {process.returncode}\n{process.stdout}\n{process.stderr}')
            evidence = gate(raw)
            expected_evidence = {'exit_code': gate_exit, 'counts': counts, 'issue_codes': sorted(codes)}
            if evidence != expected_evidence:
                raise RuntimeError(f'{name}: producer evidence changed: {evidence}')
            clean = normalized(raw.read_bytes())
            sanitized = folder / (name + '-sanitized.xml')
            sanitized.write_bytes(clean)
            if gate(sanitized) != evidence:
                raise RuntimeError(f'{name}: normalization changed gate evidence')
            generated[name] = clean
            summaries.append({'scenario': name, 'runner_exit': runner_exit, **evidence})

        # This is an intentional corruption of the passing producer report.
        root = ET.fromstring(generated['passing'])
        root.set('tests', str(int(root.get('tests')) + 1))
        contradictory = normalized(ET.tostring(root, encoding='utf-8'))
        path = folder / 'contradictory.xml'
        path.write_bytes(contradictory)
        evidence = gate(path)
        if evidence['exit_code'] != 1 or evidence['issue_codes'] != ['evidence.count_mismatch']:
            raise RuntimeError('An inflated producer total was not rejected')
        if evidence['counts'] != SCENARIOS['passing'][2]:
            raise RuntimeError('The count mutation unexpectedly changed testcase records')
        generated['contradictory'] = contradictory
        summaries.append({'scenario': 'contradictory', 'source': 'passing report with inflated root tests', **evidence})
    # Validate every real report before changing any committed fixture.
    for name, data in generated.items():
        check_fixture(name, data, args.update_fixtures)
    print(json.dumps({'producer_versions': installed, 'scenarios': summaries}, indent=2))


if __name__ == '__main__':
    main()
