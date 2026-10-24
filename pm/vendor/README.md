# browser-harness Pillow security compatibility patch

`browser_harness-0.1.13+hermes.1-py3-none-any.whl` is derived from the published
0.1.13 wheel. Its Python code and license files are byte-for-byte unchanged.
Only distribution version, Pillow requirement (12.3.0 to 13.0.0), dist-info name,
and RECORD hashes change. This keeps PM dependency validation enabled while
removing the vulnerable Pillow pin (including GHSA-8g5g-8vmr-9782).

Rebuild: `python pm/vendor/rebuild_browser_harness.py`.
The script verifies the upstream SHA-256 before modifying metadata, then writes
sorted ZIP entries with fixed timestamps and regenerated RECORD hashes.

Upstream SHA-256: `2491459e4bfc0ee8aea22dc6c4680fc0f791b7ba553446323c50d2883449d769`.
Patched SHA-256: `1845e9f89732c530e2b0246dde4dd4eb3f3862638fcae38b144e79241ece478e`.

Remove the vendored artifact and tool.uv.sources mapping when upstream publishes
a release compatible with patched Pillow. Do not remove browser support or
suppress dependency validation to retire this patch.
