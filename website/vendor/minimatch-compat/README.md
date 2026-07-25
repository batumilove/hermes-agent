# minimatch compatibility wrapper

`serve-handler@6.1.7` expects the callable CommonJS API from `minimatch@3`, whose
`brace-expansion` dependency is affected by GHSA-mh99-v99m-4gvg. This local
package preserves that callable surface while delegating to `minimatch@10.2.5`,
which resolves patched `brace-expansion@5.0.8`.

Remove this wrapper and the corresponding npm override after `serve-handler`
ships a release that natively supports a non-vulnerable minimatch dependency.
The prebuild regression test must remain green during that replacement.
