# glob 7 callback compatibility

The callback/event/synchronous walker remains upstream glob 7.2.3. The two entry match loops use minimatch 11 Matcher.pattern and Matcher.test instead of the removed RegExp._glob/String.match interface. The dependency is minimatch 11.0.3; no legacy matcher remains. LICENSE and all other upstream published files are retained. SOURCE.json binds original artifact integrity and original/patched file hashes; PATCH.diff is the complete delta.

The direct root artifact is selected only for legacy glob 7 consumers. Modern named-API glob consumers remain on glob 14.0.3. Retire this derivative when those legacy callers migrate to maintained modern APIs with equivalent callback, cleanup and Windows behavior proven. Rebuild with scripts/build/npm-compat-archives.py.
