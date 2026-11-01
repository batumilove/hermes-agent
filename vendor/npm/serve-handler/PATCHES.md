# serve-handler minimatch compatibility

This derivative retains upstream serve-handler 6.1.7 and its MIT license. The only JavaScript change selects minimatch's named function export. Its dependency is pinned to minimatch 11.0.3, fixing GHSA-g3hv-75wg-m249 without keeping the vulnerable legacy matcher. SOURCE.json records original npm artifact integrity and file hashes. PATCH.diff contains the complete delta.

The website declares the packaged archive directly and overrides serve-handler with $serve-handler, following the existing braces custody pattern. The archive contains only the upstream published files with the two documented edits. Rebuild deterministically with scripts/build/npm-compat-archives.py. Retire this derivative when a maintained upstream release adopts the current minimatch API and the real HTTP routing tests pass.
