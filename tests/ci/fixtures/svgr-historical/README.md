# Historical SVGR graph fixtures

The `.fixture` / `.fixture.gz` suffixes mark inert data, not repository installation lockfiles.
The tests restore the original filenames only inside disposable roots.
`package-lock.json.fixture.gz` stores the exact historical bytes using deterministic
stdlib gzip (`gzip.compress(raw, compresslevel=9, mtime=0)`, Python 3.14).
Compressed length: 148025 bytes; compressed SHA256:
`84890865c3c1724c773c239d9a171d0b0dd50e0ebe22ef2b066d1e5f5917a444`.
Both loaders bound the compressed read and decompressed output to the recorded
length plus one, verify both lengths and hashes, then write all original bytes.
Compression satisfies the unchanged 500 KiB tracked-file gate without exceptions;
the original uncompressed length, hash and Git provenance below are unchanged.
These are byte-for-byte copies of `website/package.json` and
`website/package-lock.json` from commit
`862586795a9a49c798698687bdfe1f96d4dd92d9` (not the supported installed graph).
They preserve the reviewed derivative's positive and adversarial controls without
requiring historical Git objects in default depth-one CI checkouts. Both tests
assert SHA256 before writing the original bytes into disposable fixture roots.
No runtime tracking checks or adversarial assertions are relaxed.

| File | Bytes | SHA256 | Git blob |
| --- | ---: | --- | --- |
| package.json | 2580 | 4bb5ff228c9a398eed2ace2c842263894259f9c0888b8f4d462d48066667436b | 812c0224fd0b906ba1bf62e2c19d706aab63e95e |
| package-lock.json | 724159 | 8e7782528c603442f0f54722efd3671cfb98de2e890d43f317bcbf44e5088ec6 | e05c35cb50f9d8bf79885ea16121dd074121ac44 |

Reproduction in a history-bearing review checkout:
`git show 862586795a9a49c798698687bdfe1f96d4dd92d9:website/<file>`.
This extraction is provenance documentation, not a test-time prerequisite.
These manifests are non-installed test data, not scanner input or an npm install
root. Run `python -m pytest tests/ci/test_svgr_directory_tracking.py
 tests/ci/test_svgr_adversarial.py` with repository pytest dependencies and Git
for canonical repository guards; historical Git objects are not required.
Retire with the historical derivative controls, not by updating them to the
supported graph and thereby removing the historical negative controls.
