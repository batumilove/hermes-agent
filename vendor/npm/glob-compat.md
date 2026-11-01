# glob 7 compatibility adapter

Minimatch 11 returns nonrecursive Matcher objects instead of regular expressions.
Glob 7 requires its callback/synchronous API, so replacing it with glob 14 breaks its consumers.
The adapter reads Matcher.pattern instead of the removed RegExp._glob and calls Matcher.test
instead of String.match. Other archive members and licensing are retained. It does not
construct regular expressions or bypass minimatch safeguards. Rebuild with
`python3 scripts/build-glob-compat.py`; original integrity and output SHA-256 are checked.
Remove this adapter when the legacy callers migrate to a maintained glob API.
