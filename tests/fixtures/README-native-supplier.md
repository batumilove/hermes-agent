# Offline native-launcher package fixture

`distlib-0.4.2.members/` holds **test-only explicit JSON/base64 member data**,
not a renamed opaque archive, production supplier or executable oracle. Unit
PE-generation tests never execute PE; native Windows subprocess tests use these
same authenticated supplier bytes and require separate native CI acceptance.
The original source, license and PE resources were extracted from the already
authenticated local Python archive whose SHA256 and member prefix are recorded
in `distlib-0.4.2.json`. Its historical fixture archive hash remains provenance,
not the hash of the new representation. Every decoded member hash is unchanged.
The source archive is not required; this directory is self-contained.

`tests.native_supplier.unpack_supplier` checks the closed member schema,
exact member inventory, every decoded digest, safe plain-file names and the
repository's explicit **0.4.2** pin before writing any supplier file. The ordinary
pytest fixture needs no environment variable, download or ambient distlib.
Explicit member records keep each reviewable tracked blob below the existing
size limit; neither archive nor size scanner receives any exception.
The existing Windows test extra 0.4.4 and production pins are unchanged.

This fixture does not prove native Windows execution or sharing semantics.

Borrowed-home owner publication must use a runnable private interpreter and
this selected supplier, not a touched executable. The holder remains inert
where it must never publish. A running repin holder must also commit a minimal
dependency generation before launcher execution: stdlib-only main code does
not bypass the real bootstrap's dependency guard. Portable prerequisite tests
exercise the original setup and real bootstrap in a fresh child; they do not
replace x64/ARM64 native launcher execution or mapped-image acceptance.
