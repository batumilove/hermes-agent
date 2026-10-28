"""Provenance imports must work in the PM snapshot, not just a checkout."""
from pathlib import Path
import os
import shutil
import subprocess

from pm.workspace import _copy_core_inputs


def test_packaged_provenance_matches_standalone_verifier():
    source = Path(__file__).resolve().parents[2]
    assert (source / "hermes_cli/fork_provenance.py").read_bytes() == (
        source / "scripts/fork_provenance.py"
    ).read_bytes()


def test_installed_snapshot_can_verify_source_provenance(tmp_path):
    source = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "workspace"
    snapshot.mkdir()
    _copy_core_inputs(source, snapshot)
    uv = shutil.which("uv")
    assert uv, "the PM build requires uv"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update(HOME=str(tmp_path / "home"), HERMES_HOME=str(tmp_path / "state"))
    venv = tmp_path / "venv"
    subprocess.run([uv, "venv", str(venv)], env=env, check=True, capture_output=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    built = subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "-e", str(snapshot)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    repo = tmp_path / "unverified-source"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github/upstream-sync-provenance.json").write_text("{}")
    code = (
        "from pathlib import Path; import hermes_cli.version_provenance as p; "
        f"assert Path(p.__file__).is_relative_to(Path({str(snapshot)!r})); "
        f"assert p.imported_release_version(Path({str(repo)!r})) is None; "
        "print(p.__file__)"
    )
    result = subprocess.run([str(python), "-I", "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
