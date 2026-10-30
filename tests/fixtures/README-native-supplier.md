# Offline native-launcher package fixture

`distlib-0.4.2.tar.gz` is a **test-only package fixture**, not a production
supplier or executable oracle. No PE is executed. It contains the original
standalone distlib Python source, license and PE resources extracted from the
already authenticated local Python archive whose SHA256 and original member
prefix are recorded in `distlib-0.4.2.json`. The archive digest was checked
against the preserved packet manifest before extraction. The source archive is
not required to run tests; this directory is self-contained.

`tests.native_supplier.unpack_supplier` checks the compressed archive digest,
exact member inventory, every member digest, safe plain-file names and the
repository's explicit **0.4.2** pin before use. The ordinary pytest fixture
`native_supplier` needs no environment variable, download or ambient distlib.
The existing Windows test extra 0.4.4 and production pins are unchanged.

Diagnostic contract disposition: the old ambient-loader/MZ-only maker fixtures
were invalid under explicit selected-supplier and native payload validation.
The two observer controls now use the selected loader and real resource/ZIP
writer, retain generator non-consumption and throwing-observer assertions, and
validate generated payloads. Early supplier refusal consistently reports
`invalid-selected-supplier`, including missing or invalid selected resources.
Historical originals and their three failures remain in the base commit and
RED receipts. Snapshot byte-capture tests may still use arbitrary bytes: they
test capture, not native acceptance. None of this proves native Windows
supplier availability, import, execution, sharing semantics or acceptance.
