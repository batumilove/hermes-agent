# Repository brace-expansion compatibility adapter

Current ESLint and Electron packaging dependencies still request legacy
`brace-expansion` v1/v2 through old `minimatch` releases. Those release lines
have no patched version for GHSA-mh99-v99m-4gvg.

This private package reports the patched `5.0.8` identity and delegates to the
genuine `brace-expansion@5.0.8` implementation. It exports both the legacy
callable CommonJS contract and the modern named API, so minimatch 3, 5, 9, and
10 consumers can share it safely. A root npm override routes every
`brace-expansion` dependency through the adapter.

`scripts/brace-expansion-compat.test.cjs` verifies resolution and both API
contracts from every affected ESLint and Electron consumer. Workspace checks
and desktop packaging tests exercise their real minimatch call paths.

Remove this adapter when all consumers accept patched brace-expansion directly.
