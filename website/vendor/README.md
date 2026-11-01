# serve-handler compatibility artifact

`serve-handler-6.1.7-hermes.1.tgz` preserves the MIT-licensed upstream 6.1.7
archive except its package version, minimatch dependency, and the CommonJS
import used by its matcher. Minimatch 11 exposes its callable as a named export;
the unmodified server calls the module object and fails. This adapter permits
the patched matcher without changing serving logic or removing its features.

Reproduce with Python 3.14.9 using `python scripts/build-serve-handler-compat.py`
from `website/`. The builder verifies the upstream registry SHA-512, preserves
all license/source files, normalizes archive metadata, and verifies the resulting
SHA-256 recorded in `serve-handler-compat.json`. No install hook runs this script.

Retire this artifact when an upstream serve-handler release supports the fixed
matcher. This is a prepared candidate: dependency resolution and functional
serving tests remain pending the explicitly requested cooldown exceptions.
