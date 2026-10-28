"""Exercise the unmodified wrapper's env-i exec, with a harmless runner stand-in."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("flag", ["1", None, "0", ""])
def test_actual_wrapper_diagnostic_boundary(tmp_path, flag):
    source = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # Copy the actual entrypoint, not an extracted/reimplemented allowlist.
    for name in ("run_tests.sh", "_activation.sh"):
        shutil.copyfile(source / "scripts" / name, scripts / name)
    home = tmp_path / "home"
    home.mkdir()
    interpreter = tmp_path / "python-fixture"
    interpreter.write_text(
        "#!/bin/bash\n"
        "if [[ $1 == -c ]]; then exit 0; fi\n"
        "if [[ $1 == -m && $2 == compileall ]]; then exit 0; fi\n"
        f"exec {sys.executable} \"$@\"\n"
    )
    interpreter.chmod(0o700)
    # Only interpreter discovery/precompile and runner are stand-ins. Bash,
    # the full production wrapper and env-i execute unchanged. The runner
    # exercises real Journey serialization downstream, without Windows calls.
    (scripts / "run_tests_parallel.py").write_text(
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from pathlib import Path\n"
        "from tests.e2e.core.windows_update._machine import Machine, Journey\n"
        "m = Machine(Path('machine'), 'profile', 'unused')\n"
        "j = Journey(m)\n"
        "def fail(): raise RuntimeError('fixture failure')\n"
        "j.step('install', fail)\n"
        "print(json.dumps({'flag': os.environ.get('HERMES_LAUNCHER_DIAGNOSTIC'), "
        "'strict': os.environ.get('HERMES_E2E_STRICT_ACCEPTANCE'), "
        "'secret': 'OPENAI_API_KEY' in os.environ, "
        "'unrelated': 'HERMES_TEST_UNRELATED_SECRET' in os.environ, "
        "'receipt': (m.logs / 'install-prerequisite.json').is_file(), "
        "'gate': j.ok('install')}))\n"
    )
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "HERMES_PYTHON": str(interpreter), "HERMES_E2E_STRICT_ACCEPTANCE": "1",
           "OPENAI_API_KEY": "synthetic-credential-sentinel",
           "HERMES_TEST_UNRELATED_SECRET": "synthetic-unrelated-sentinel"}
    if flag is not None:
        env["HERMES_LAUNCHER_DIAGNOSTIC"] = flag
    result = subprocess.run(["bash", str(scripts / "run_tests.sh")],
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout.splitlines()[-1])
    assert row["flag"] == (flag or None)
    assert row["strict"] == "1"
    assert not row["secret"] and not row["unrelated"]
    assert row["receipt"] is (flag == "1")
    assert not row["gate"]
