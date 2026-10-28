"""Fixed-source acceptance checks; this control script is NOT application source."""
from __future__ import annotations
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess

SOURCE = '381d8e15a0f568c3a2e21be3dff99d221595a3c0'
TREE = 'b7a5493e92968857f2143cbec0e7221e6f83558f'


def admit(source: str, tree: str) -> tuple[str, str]:
    if source != SOURCE or tree != TREE:
        raise ValueError('source commit/tree is not the reviewed PR439 allowlist entry')
    return source, tree


def verify_identity(stamp: dict, marker: dict, installed: str) -> None:
    if stamp.get('commit') != SOURCE or stamp.get('dirty') is not False:
        raise ValueError('unbound or dirty install stamp')
    version = stamp.get('packageVersion', '')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+\.g' + SOURCE, version):
        raise ValueError('package version does not bind exact reconciled source')
    if installed != version or marker.get('revision') != SOURCE or marker.get('version') != version:
        raise ValueError('installed package or immutable marker differs')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['admit', 'runtime'])
    parser.add_argument('--source', default=SOURCE)
    parser.add_argument('--tree', default=TREE)
    args = parser.parse_args()
    admit(args.source, args.tree)
    if args.mode == 'admit':
        return
    stamp = json.loads(Path('/opt/hermes/install-stamp.json').read_text())
    marker = json.loads(Path('/etc/hermes/image-provenance.json').read_text())
    installed = importlib.metadata.version('hermes-agent')
    verify_identity(stamp, marker, installed)
    imports = ['cron.scheduler', 'gateway.run', 'tools.registry', 'hermes_cli.version_info']
    for name in imports:
        module = importlib.import_module(name)
        if not module.__file__ or not Path(module.__file__).resolve().is_relative_to('/opt/hermes'):
            raise ValueError('module imported outside immutable application root: ' + name)
    from hermes_cli.version_info import get_version_info
    runtime_version = get_version_info()
    if runtime_version.commit != SOURCE:
        raise ValueError('runtime version resolver reports different source')
    cli = subprocess.check_output(['/opt/hermes/.venv/bin/hermes', '--version'], text=True, timeout=60)
    if runtime_version.derived_version not in cli:
        raise ValueError('CLI does not report the resolved exact-source version')
    print(json.dumps({'source_sha': SOURCE, 'source_tree': TREE, 'installed_version': installed,
                      'imports': imports, 'cli_version': cli.strip(), 'marker': marker}, sort_keys=True))


if __name__ == '__main__':
    main()
