"""Check wheel package bytes, metadata, entry point and license against source."""
import configparser
from email.parser import Parser
import hashlib
from pathlib import Path
import sys
import tomllib
from zipfile import ZipFile

def check(condition, message):
    if not condition:
        raise SystemExit(message)

folder = Path(sys.argv[1])
wheels = list(folder.glob("junit_evidence_gate-*.whl"))
check(len(wheels) == 1, "Expected exactly one junit-evidence-gate wheel")
wheel = wheels[0]
project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
with ZipFile(wheel) as archive:
    names = archive.namelist()
    sources = {str(path).replace("\\", "/"): path for path in Path("junit_evidence_gate").rglob("*.py")}
    packaged = {name for name in names if name.startswith("junit_evidence_gate/")}
    check(packaged == set(sources), "Wheel package file set differs from source")
    for name, source in sources.items():
        check(archive.read(name) == source.read_bytes(), "Wheel source differs: " + name)
    headers = Parser().parsestr(archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode("utf-8"))
    check(headers["Name"] == project["name"] and headers["Version"] == project["version"], "Wheel name or version differs")
    check(headers["Requires-Python"] == project["requires-python"], "Python requirement differs")
    check(not headers.get_all("Requires-Dist"), "Runtime dependencies were added")
    entries = configparser.ConfigParser()
    entries.read_string(archive.read(next(name for name in names if name.endswith(".dist-info/entry_points.txt"))).decode("utf-8"))
    check(entries["console_scripts"]["junit-evidence-gate"] == project["scripts"]["junit-evidence-gate"], "Console entry point differs")
    license_name = next(name for name in names if name.endswith("/LICENSE"))
    check(archive.read(license_name) == Path("LICENSE").read_bytes(), "Wheel license differs")
digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
(folder / "SHA256SUMS").write_text(digest + "  " + wheel.name + "\n", encoding="utf-8")
print("Verified wheel " + wheel.name + " SHA256 " + digest)
