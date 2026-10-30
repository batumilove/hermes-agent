"""Fail closed on missing/tampered derivative tracking and upstream advisories.

The added lockfile is an explicitly labelled advisory projection, NOT the installed
package identity. It preserves advisory recognition of byte-identical upstream JS.
"""
import argparse
import hashlib
import json
from pathlib import Path

RUNTIME = '4a9a9511fdeee0ecf9ed25837b44f1c8b82b3a069bd9a34247077ff5034b9078'
INTEGRITY = 'sha512-Ywtl837OGO9pTLIN/onoWLmDQ4zFUycI1g76vuKGEz6evR/ZTJlJuz3G/fIkb6OVBJ2g0o6CGJzaEjfmEo3AHA=='
NAME = '@svgr/plugin-svgo'
TARBALL = 'https://registry.npmjs.org/@svgr/plugin-svgo/-/plugin-svgo-8.1.0.tgz'
SOURCE = {'upstream': {'name': '@svgr/plugin-svgo', 'version': '8.1.0', 'git_commit': '772592e042be5063e782bfb8711d024b2fefc6b8', 'tarball': 'https://registry.npmjs.org/@svgr/plugin-svgo/-/plugin-svgo-8.1.0.tgz', 'sha512': 'Ywtl837OGO9pTLIN/onoWLmDQ4zFUycI1g76vuKGEz6evR/ZTJlJuz3G/fIkb6OVBJ2g0o6CGJzaEjfmEo3AHA=='}, 'original_sha256': {'LICENSE': '7bc7d9d513d14b0951dfd29718d6bdc434545c11621bead0909f625f0e135326', 'dist/index.js': '4a9a9511fdeee0ecf9ed25837b44f1c8b82b3a069bd9a34247077ff5034b9078', 'package.json': '31492e34b636250222efad5010097b13f11a389cb5cfdf7b380165fc8d1e65d5', 'dist/index.js.map': '6f155a164e271db4d6b37ca50b072c1ebdae30241403b425bfd837d9716367bf', 'README.md': '08d1e93b540744ce1acfa246b984e8dac6a308fa93a11db7a73621cfe4f5d522', 'dist/index.d.ts': '7bc8cfe8d679541e14679f1b0510bf498247e30d38cd4b36f80ffb25934b5006'}, 'patched_sha256': {'LICENSE': '7bc7d9d513d14b0951dfd29718d6bdc434545c11621bead0909f625f0e135326', 'dist/index.js': '4a9a9511fdeee0ecf9ed25837b44f1c8b82b3a069bd9a34247077ff5034b9078', 'package.json': '98fa17ebc173ffa42f9db37d7110ccaf8b385e6a2d8f1dbed71804fa7cd11e4b', 'dist/index.js.map': '6f155a164e271db4d6b37ca50b072c1ebdae30241403b425bfd837d9716367bf', 'README.md': '08d1e93b540744ce1acfa246b984e8dac6a308fa93a11db7a73621cfe4f5d522', 'dist/index.d.ts': '7bc8cfe8d679541e14679f1b0510bf498247e30d38cd4b36f80ffb25934b5006'}}
TRACKING = {'owner': 'batumilove/hermes-agent fork maintainers', 'custody': 'website/vendor/svgr-plugin-svgo; directory identity landed in PR447', 'agePolicy': 'Inherited qs6.16.2 exception unchanged; no additional release-age exception. Registry cooldowns unchanged.', 'retirement': 'Replace only with age-eligible upstream release removing unused deepmerge; remove derivative, override, tracking together after review.', 'upstreamName': '@svgr/plugin-svgo', 'upstreamVersion': '8.1.0', 'derivativeName': '@hermes-security/svgr-plugin-svgo', 'derivativeVersion': '8.1.0-hermes.1'}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def stage(root, installed=False):
    root = root.absolute()
    source = root / 'website/vendor/svgr-plugin-svgo'
    require(not source.is_symlink() and source.resolve() == source, 'noncanonical directory custody')
    tracking = json.loads((source / 'TRACKING.json').read_text())
    require(tracking == TRACKING, 'approved maintenance policy or identity drift')
    metadata = json.loads((source / 'SOURCE.json').read_text())
    require(metadata == SOURCE, 'upstream source binding drift')
    for name, digest in SOURCE['patched_sha256'].items():
        path = source / name
        require(not path.is_symlink() and path.resolve().is_relative_to(source), 'source symlink escape')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'directory payload hash drift')
    manifest = json.loads((source / 'package.json').read_text())
    require(manifest['name'] == '@hermes-security/svgr-plugin-svgo' and manifest['version'] == '8.1.0-hermes.1', 'directory identity drift')
    require('deepmerge' not in manifest['dependencies'], 'unused dependency returned')
    lock = json.loads((root / 'website/package-lock.json').read_text())
    package_json = json.loads((root / 'website/package.json').read_text())
    require(package_json['dependencies'].get(NAME) == 'file:vendor/svgr-plugin-svgo', 'root dependency custody drift')
    require(package_json['overrides'].get(NAME) == '$' + NAME, 'override custody drift')
    require(lock['packages']['']['dependencies'].get(NAME) == 'file:vendor/svgr-plugin-svgo', 'lock root dependency custody drift')
    require(lock['packages']['node_modules/@svgr/plugin-svgo'] == {'resolved': 'vendor/svgr-plugin-svgo', 'link': True}, 'resolved directory binding drift')
    entry = lock['packages']['vendor/svgr-plugin-svgo']
    require(entry['name'] == manifest['name'] and entry['version'] == manifest['version'] and entry['dependencies'] == manifest['dependencies'], 'lock source identity drift')
    if installed:
        slot = root / 'website/node_modules/@svgr/plugin-svgo'
        require(slot.is_symlink() and slot.resolve() == source, 'installed directory provenance drift')
        for name, digest in SOURCE['patched_sha256'].items():
            require(hashlib.sha256((slot / name).read_bytes()).hexdigest() == digest, 'installed payload hash drift')
    destination = root / '.osv-lockfiles/svgr-upstream/package-lock.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    projection = {'name': 'advisory-projection-not-installed-svgr-upstream', 'version': '1.0.0', 'lockfileVersion': 3, 'packages': {'': {'name': 'advisory-projection-not-installed-svgr-upstream','version':'1.0.0'}, 'node_modules/@svgr/plugin-svgo': {'name': NAME, 'version': '8.1.0', 'resolved': TARBALL, 'integrity': INTEGRITY}}}
    destination.write_text(json.dumps(projection, indent=2) + '\n')
    return destination

def validate_scan(code, payload):
    require(code == 0, 'upstream scanner failed or reported vulnerability')
    require(isinstance(payload, dict) and set(payload) <= {'results', 'experimental_config'} and isinstance(payload.get('results'), list), 'invalid scanner result')
    if 'experimental_config' in payload:
        require(payload['experimental_config'] == {'licenses': {'summary': False, 'allowlist': None}}, 'unrecognized scanner configuration')
    # OSV 2.5.1 omits clean packages entirely: the retained real clean receipt
    # is results=[], NOT a fabricated package record. Nonempty results must bind
    # exactly to this single-input projection and expected original npm identity.
    require(len(payload['results']) <= 1, 'ambiguous scanner sources')
    for result in payload['results']:
        require(isinstance(result, dict) and set(result) == {'source', 'packages'}, 'invalid source result')
        source = result['source']
        require(isinstance(source, dict) and set(source) == {'path', 'type'} and source['type'] == 'lockfile', 'invalid scanner source')
        require(source['path'] in {'.osv-lockfiles/svgr-upstream/package-lock.json', '/src/.osv-lockfiles/svgr-upstream/package-lock.json', '/github/workspace/.osv-lockfiles/svgr-upstream/package-lock.json'}, 'unexpected scanner source')
        require(isinstance(result['packages'], list) and len(result['packages']) == 1, 'invalid package results')
        for package in result['packages']:
            require(isinstance(package, dict) and {'package', 'vulnerabilities'} <= set(package) <= {'package', 'vulnerabilities', 'groups', 'licenses'}, 'invalid package result')
            require(package['package'] == {'name': NAME, 'version': '8.1.0', 'ecosystem': 'npm'}, 'unexpected scanner package identity')
            require(isinstance(package['vulnerabilities'], list), 'invalid vulnerabilities schema')
            require(not package['vulnerabilities'], 'upstream vulnerability blocks derivative')
            for key in ('groups', 'licenses'):
                if key in package:
                    require(package[key] == [], 'unexpected scanner metadata')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'check'])
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--exit-code', type=int)
    parser.add_argument('--results', type=Path)
    args = parser.parse_args()
    if args.mode == 'stage':
        print(stage(args.root, installed=args.installed))
    else:
        require(args.results is not None, 'missing scanner results path')
        validate_scan(args.exit_code, json.loads(args.results.read_text()))
        print('Upstream identity advisory scan: zero findings')

if __name__ == '__main__':
    main()
