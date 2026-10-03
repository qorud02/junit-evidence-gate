"""Check distributable archives, extracted tests, and an installed CLI offline."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import venv


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}"
        )
    return result


def verify(dist):
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one source archive")
    wheel, sdist = wheels[0].resolve(), sdists[0].resolve()
    checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (wheel, sdist)}
    with tempfile.TemporaryDirectory(prefix="junit-dist-") as temporary:
        temp = Path(temporary)
        with tarfile.open(sdist) as archive:
            for member in archive.getmembers():
                target = (temp / member.name).resolve()
                if not target.is_relative_to(temp.resolve()) or not (
                    member.isdir() or member.isfile()
                ):
                    raise ValueError(f"Unsupported archive entry: {member.name}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source:
                        target.write_bytes(source.read())
        roots = [path for path in temp.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise ValueError("Expected one source archive root")
        source = roots[0]
        required = (
            "examples/green.xml", "examples/suite-error.xml", "examples/run_demo.py",
            "docs/compatibility.md", "CONTRIBUTING.md",
        )
        for relative in required:
            if not (source / relative).is_file():
                raise ValueError(f"Source archive is missing {relative}")
        tests = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], source)
        demo = run([sys.executable, "-B", "examples/run_demo.py"], source)
        print(tests.stderr.strip())
        print(demo.stdout.strip())
        environment = temp / "installed"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], temp)
        imported = run(
            [str(python), "-I", "-c", "import junit_evidence_gate; print(junit_evidence_gate.__file__)"],
            temp,
        )
        location = Path(imported.stdout.strip()).resolve()
        if not location.is_relative_to(environment.resolve()):
            raise ValueError(f"Import escaped installed environment: {location}")
        command = scripts / ("junit-evidence-gate.exe" if os.name == "nt" else "junit-evidence-gate")
        checks = []
        for fixture, expected in (
            ("green.xml", 0), ("contradictory.xml", 1), ("suite-error.xml", 1),
        ):
            result = subprocess.run(
                [str(command), str(source / "examples" / fixture)], cwd=temp,
                capture_output=True, text=True,
            )
            if result.returncode != expected:
                raise ValueError(f"{fixture}: expected {expected}, got {result.returncode}: {result.stderr}")
            report = json.loads(result.stdout)
            if report["schema_version"] != 1:
                raise ValueError("Unexpected CLI schema")
            if fixture == "suite-error.xml" and not any(
                issue["code"] == "evidence.structure" for issue in report["issues"]
            ):
                raise ValueError("Installed CLI lost the result-structure guard")
            checks.append({"fixture": fixture, "exit_code": result.returncode})
        (dist / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
            encoding="utf-8",
        )
        print(json.dumps({
            "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "sdist": sdist.name, "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
            "import_from_installed_environment": True, "installed_cli": checks,
        }, indent=2))


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve())
