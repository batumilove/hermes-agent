"""Portable prerequisite checks; native PE execution still requires Windows CI."""
import subprocess

from hermes_cli import _launchers
from tests.hermes_cli import test_borrowed_home_launch as borrowed
from tests.hermes_cli import test_source_launcher_publication as source

_no_tool_downloads = borrowed._no_tool_downloads


def test_borrowed_owner_has_runnable_selected_supplier(tmp_path, monkeypatch):
    original = _launchers.ensure_install_launchers
    observed = []

    def inspect_owner(repo, destination, **kwargs):
        if observed:
            return original(repo, destination, **kwargs)
        python = _launchers.resolve_store_python(repo)
        assert python is not None
        assert python.stat().st_size > 0, 'owner interpreter must be runnable, not zero-byte'
        result = subprocess.run([str(python), '-c', 'print("owner-ready")'],
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'owner-ready'
        assert _launchers._store_script_maker(python) is not None
        observed.append(python)
        return original(repo, destination, **kwargs)

    monkeypatch.setattr(_launchers, 'ensure_install_launchers', inspect_owner)
    borrowed.test_a_borrower_holding_the_checkout_never_becomes_its_owner(tmp_path, monkeypatch)
    assert len(observed) == 1


def test_repin_holder_commits_dependencies_before_boot(tmp_path, monkeypatch):
    import ast
    import inspect
    # Execute the real test's setup, stopping before native publication on POSIX.
    tree = ast.parse(inspect.getsource(source.test_repin_without_distlib_retires_stale_native_launcher))
    body = tree.body[0].body
    cutoff = next(i for i, node in enumerate(body)
                  if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'local'
                                                         for t in node.targets))
    namespace = dict(vars(source), tmp_path=tmp_path, monkeypatch=monkeypatch)
    exec(compile(ast.Module(body=body[:cutoff], type_ignores=[]), '<repin-setup>', 'exec'), namespace)
    repo, home = namespace['repo'], namespace['home']
    from pm.environments import install_state_dir
    import json
    facts = install_state_dir(repo) / 'facts.json'
    assert facts.is_file(), 'repin holder must commit its dependency generation before boot'
    state = json.loads(facts.read_text())
    assert state['packages']['venv']['environment']
    # Keep activation in a fresh child: it mutates sys.path by design.
    import sys
    script = repo / 'holder-bootstrap.py'
    script.write_text(
        'import sys\n'
        f'sys.base_prefix = {str(home / "tools/python-fixture")!r}\n'
        + _launchers._launcher_script('hermes', repo, home), encoding='utf-8')
    result = subprocess.run([str(source.Path(sys._base_executable)), str(script)],
                            cwd=repo, input='', capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'ready'
