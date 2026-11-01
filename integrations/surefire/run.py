"""Run pinned Maven/JUnit test classes and verify sanitized Surefire evidence."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURES = ROOT / 'examples/producers/surefire'
VERSIONS = {'maven': '3.10.0', 'surefire': '3.6.0', 'junit': '6.1.3', 'compiler': '3.14.1'}
SCENARIOS = {
    'passing': ('PassingTest', 0, 0,
                {'records': 3, 'unique': 3, 'executed': 2, 'passed': 2,
                 'failed': 0, 'errors': 0, 'skipped': 1}, set()),
    'failing': ('FailingTest', 1, 1,
                {'records': 3, 'unique': 3, 'executed': 3, 'passed': 1,
                 'failed': 1, 'errors': 1, 'skipped': 0}, {'policy.failed'}),
    'flaky': ('FlakyTest', 0, 1,
              {'records': 2, 'unique': 2, 'executed': 2, 'passed': 2,
               'failed': 0, 'errors': 0, 'skipped': 0}, {'evidence.unsupported_result'}),
    'retry-failure': ('RetryFailureTest', 1, 1,
                      {'records': 2, 'unique': 2, 'executed': 2, 'passed': 0,
                       'failed': 1, 'errors': 1, 'skipped': 0},
                      {'evidence.unsupported_result', 'policy.failed'}),
    'parameterized': ('ParameterCasesTest', 0, 0,
                      {'records': 2, 'unique': 2, 'executed': 2, 'passed': 2,
                       'failed': 0, 'errors': 0, 'skipped': 0}, set()),
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


def local(tag):
    return tag.rsplit('}', 1)[-1]


def normalized(data):
    root = ET.fromstring(data)
    for parent in root.iter():
        for node in list(parent):
            if local(node.tag) == 'properties':
                parent.remove(node)
        for attribute in ('time', 'timestamp', 'hostname'):
            parent.attrib.pop(attribute, None)
        if local(parent.tag) in {'failure', 'error', 'flakyFailure', 'flakyError',
                                  'rerunFailure', 'rerunError', 'stackTrace', 'system-out', 'system-err'}:
            parent.text = None
        cases = [node for node in parent if local(node.tag) == 'testcase']
        if cases:
            for node in cases:
                parent.remove(node)
            parent.extend(sorted(cases, key=lambda node: (node.get('classname', ''), node.get('name', ''))))
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def check_fixture(name, data, update):
    fixture = FIXTURES / (name + '.xml')
    if update:
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(data)
    elif not fixture.is_file() or fixture.read_bytes() != data:
        raise RuntimeError(f'{fixture.name} differs; review real producer output before --update-fixtures')


def verify_project_versions(path):
    root = ET.parse(path).getroot()
    namespace = {'m': 'http://maven.apache.org/POM/4.0.0'}
    expected = {'maven-surefire-plugin': VERSIONS['surefire'],
                'maven-compiler-plugin': VERSIONS['compiler'],
                'junit-jupiter': VERSIONS['junit']}
    found = {}
    for node in root.findall('.//m:plugin', namespace) + root.findall('.//m:dependency', namespace):
        artifact = node.findtext('m:artifactId', namespaces=namespace)
        if artifact in expected:
            if artifact in found:
                raise ValueError('Producer coordinates must be unambiguous')
            found[artifact] = node.findtext('m:version', namespaces=namespace)
    if found != expected:
        raise ValueError('Producer dependency/plugin versions differ from the fixture contract')
    for key in ('source', 'target'):
        if root.findtext('m:properties/m:maven.compiler.' + key, namespaces=namespace) != '21':
            raise ValueError('Producer compiler source and target must be Java 21')


def maven_launcher(value):
    # Each scenario runs from a new temporary directory, not the caller's cwd.
    return str(Path(shutil.which(value) or value).resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maven', default=shutil.which('mvn'), help='Path to the Maven 3.10.0 launcher')
    parser.add_argument('--settings', type=Path, help='Optional local network settings; never copied into fixtures')
    parser.add_argument('--repository', type=Path, default=HERE / 'generated/maven-repository', help='Local dependency-cache directory')
    parser.add_argument('--offline', action='store_true', help='Use only dependencies already in the local cache')
    parser.add_argument('--update-fixtures', action='store_true', help='Replace fixtures only after all evidence checks pass')
    parser.add_argument('--keep-reports', type=Path, help='Keep sanitized XML and path-redacted Maven logs')
    args = parser.parse_args()
    if not args.maven:
        parser.error('Install Maven 3.10.0 and Java 21, or pass --maven')
    args.maven = maven_launcher(args.maven)
    verify_project_versions(HERE / 'pom.xml')
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'MAVEN_OPTS', 'MAVEN_ARGS', 'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS'}}
    environment['MAVEN_SKIP_RC'] = '1'
    version = subprocess.run([args.maven, '--version'], env=environment,
                             capture_output=True, text=True, encoding='utf-8', timeout=30)
    version_text = re.sub(r'\x1b\[[0-9;]*m', '', version.stdout)
    maven_version = re.search(r'Apache Maven (\S+)', version_text)
    java_version = re.search(r'Java version: (\d+)', version_text)
    if version.returncode or maven_version is None or maven_version.group(1) != VERSIONS['maven']:
        raise RuntimeError('This producer fixture is pinned to Maven 3.10.0')
    if java_version is None or int(java_version.group(1)) < 21:
        raise RuntimeError('The producer fixture requires a Java 21 or newer compiler')
    repository = args.repository.resolve()
    repository.mkdir(parents=True, exist_ok=True)
    settings = (args.settings or HERE / 'settings.xml').resolve()
    generated, summaries = {}, []
    with tempfile.TemporaryDirectory(prefix='junit-surefire-producer-') as temporary:
        folder = Path(temporary)
        for name, (class_name, runner_exit, gate_exit, counts, codes) in SCENARIOS.items():
            project = folder / name
            project.mkdir()
            shutil.copyfile(HERE / 'pom.xml', project / 'pom.xml')
            shutil.copytree(HERE / 'src', project / 'src')
            command = [args.maven, '--batch-mode', '--no-transfer-progress',
                       '-s', str(settings), '-gs', str(HERE / 'settings.xml'),
                       '-Dmaven.repo.local=' + str(repository), '-Dtest=' + class_name,
                       '-Dstyle.color=never', 'test']
            if name in {'flaky', 'retry-failure'}:
                command.insert(-1, '-Dsurefire.rerunFailingTestsCount=1')
            if args.offline:
                command.insert(1, '--offline')
            process = subprocess.run(command, cwd=project, env=environment,
                                     capture_output=True, text=True, encoding='utf-8', timeout=180)
            if args.keep_reports:
                args.keep_reports.mkdir(parents=True, exist_ok=True)
                log = (process.stdout + process.stderr).replace(str(project), '<fixture-project>')
                log = log.replace(str(repository), '<dependency-cache>').replace(str(ROOT), '<checkout>')
                (args.keep_reports / (name + '.log')).write_text(log, encoding='utf-8')
            reports = list((project / 'target/surefire-reports').glob('TEST-*.xml'))
            if process.returncode != runner_exit or len(reports) != 1:
                raise RuntimeError(f'{name}: unexpected Maven result {process.returncode}; reports={len(reports)}\n{process.stdout}\n{process.stderr}')
            evidence = gate(reports[0])
            if evidence != {'exit_code': gate_exit, 'counts': counts, 'issue_codes': sorted(codes)}:
                raise RuntimeError(f'{name}: producer evidence changed: {evidence}')
            clean = normalized(reports[0].read_bytes())
            if str(project).encode() in clean or str(ROOT).encode() in clean or str(repository).encode() in clean:
                raise RuntimeError('Sanitized fixture still contains a local path')
            path = folder / (name + '.xml')
            path.write_bytes(clean)
            if gate(path) != evidence:
                raise RuntimeError(f'{name}: normalization changed gate evidence')
            generated[name] = clean
            if args.keep_reports:
                (args.keep_reports / path.name).write_bytes(clean)
            summaries.append({'scenario': name, 'runner_exit': runner_exit, **evidence})

        root = ET.fromstring(generated['passing'])
        root.set('tests', str(int(root.get('tests')) + 1))
        generated['contradictory'] = normalized(ET.tostring(root, encoding='utf-8'))
        root = ET.fromstring(generated['passing'])
        root.append(copy.deepcopy(root.find('testcase')))
        root.set('tests', str(int(root.get('tests')) + 1))
        generated['duplicate'] = normalized(ET.tostring(root, encoding='utf-8'))
        for name, expected_code in (('contradictory', 'evidence.count_mismatch'), ('duplicate', 'evidence.duplicate')):
            path = folder / (name + '.xml')
            path.write_bytes(generated[name])
            evidence = gate(path)
            if evidence['exit_code'] != 1 or evidence['issue_codes'] != [expected_code]:
                raise RuntimeError(f'{name}: intentional evidence corruption was not rejected: {evidence}')
            summaries.append({'scenario': name, 'source': 'intentional passing-report mutation', **evidence})
    for name, data in generated.items():
        check_fixture(name, data, args.update_fixtures)
    print(json.dumps({'producer_versions': VERSIONS,
                      'scenarios': summaries}, indent=2))


if __name__ == '__main__':
    main()
