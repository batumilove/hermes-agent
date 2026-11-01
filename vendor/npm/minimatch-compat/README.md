# Legacy minimatch calling convention

This first-party adapter exports minimatch 11.0.3's callable `minimatch` function,
including its attached `Minimatch`, `match`, `filter`, `defaults`, `braceExpand`,
`sep` and `GLOBSTAR` helpers. All matching runs in the upstream patched package;
no vulnerable matcher code is copied. It preserves the CommonJS interface used
by glob 7, filelist 1 and serve-handler 6 while resolving GHSA-g3hv-75wg-m249.

Root and website declare this directory as a direct dependency. Their
`install-links=true` setting materializes it beside its locked dependency rather
than linking outside the module tree. No generated archive is committed.
Remove this adapter when the legacy consumers migrate to the named-export API
and their integration tests pass.
