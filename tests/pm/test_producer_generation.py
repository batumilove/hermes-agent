"""Producer leases survive reselection without leaking import paths to children."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETUP = """
import json
import os
import site
import sys
from pathlib import Path
from hermes_constants import set_hermes_home_override

home = Path(sys.argv[1])
set_hermes_home_override(home)
from pm import environments as runtime
from hermes_cli.runtime_state import leases_held
from tools.environments.local import build_subprocess_env

project = Path.cwd()
state = runtime.install_state_dir(project)
assert state.is_relative_to(home)

def generation(name):
    venv = state / "environments" / name / "venv"
    packages = runtime.site_packages(venv)
    packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(
        f"version = {sys.version_info.major}.{sys.version_info.minor}\\n",
        encoding="utf-8",
    )
    (venv.parent / ".lease-managed").touch()
    return venv, packages

def select(venv):
    runtime.runtime_facts_path(project).write_text(
        json.dumps({"packages": {"venv": {"environment": str(venv)}}}),
        encoding="utf-8",
    )

a, asp = generation("A")
b, bsp = generation("B")
c, csp = generation("C")
select(a)
runtime.activate_dependencies(project)
assert leases_held(a.parent)
select(b)
"""


def run_scenario(tmp_path, code):
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SETUP) + textwrap.dedent(code), str(tmp_path)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_selected_generation_change_strips_only_producer_paths(tmp_path):
    run_scenario(tmp_path, """
        import subprocess
        (asp / "producer_only.py").write_text("value = 1", encoding="utf-8")
        user = home / "user"
        user.mkdir()
        (user / "user_module.py").write_text("value = 2", encoding="utf-8")
        descendant = asp / "plugin"
        descendant.mkdir()
        kept = ["", str(user), str(csp), str(descendant), ""]
        inherited = os.pathsep.join([str(project), str(asp), str(bsp), *kept])
        for activate_b in (False, True):
            if activate_b:
                runtime.activate_dependencies(project)
                assert leases_held(b.parent)
            env = build_subprocess_env(base=dict(os.environ, PYTHONPATH=inherited))
            assert env["PYTHONPATH"] == os.pathsep.join(kept), env["PYTHONPATH"]
            assert leases_held(a.parent)
            child = subprocess.run(
                [sys.executable, "-c",
                 "import importlib.util, user_module; "
                 "assert importlib.util.find_spec('producer_only') is None; "
                 "assert user_module.value == 2"],
                cwd=home, env=env, capture_output=True, text=True, timeout=15, check=False,
            )
            assert child.returncode == 0, child.stderr
    """)


@pytest.mark.parametrize("failure", ["missing_packages", "late_activation"])
def test_failed_activation_releases_only_new_lease(tmp_path, failure):
    setup_failure = (
        "bsp.rmdir()"
        if failure == "missing_packages"
        else "runtime.venv_bin_dir = lambda _: (_ for _ in ()).throw(RuntimeError('late failure'))"
    )
    run_scenario(tmp_path, setup_failure + """
before = getattr(runtime, "_activated_site_packages", ())
try:
    runtime.activate_dependencies(project)
except RuntimeError:
    pass
else:
    raise AssertionError("activation unexpectedly succeeded")
assert getattr(runtime, "_activated_site_packages", ()) == before
assert not leases_held(b.parent), "failed activation retained its lease"
assert leases_held(a.parent), "failed activation released the prior producer lease"
""")
