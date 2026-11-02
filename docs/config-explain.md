# Bounded config explanation

`hermes config explain approvals.destructive_slash_confirm --json` explains the
single supported non-secret boolean in fresh CLI processes. Other keys and worker
snapshots are unsupported. Output identifies profile/home, default, user and
managed layers and current source-byte identity; it does not attest loaded gateway
state. Explicit false is distinct from absence. Malformed or reference-valued
boolean layers fail closed; malformed managed input can therefore differ from the
normal loader's fail-open behavior. No dotenv expansion, provider discovery,
configuration migration or home initialization is needed by the explanation path.

The already-loaded `hermes_cli` package owns a single stdlib-only admission parser,
shared by the pre-bootstrap gate and explanation entry. Its bounded global-option
snapshot is checked against the real CLI parser: required/optional values, equals,
short clusters, unambiguous long abbreviations, profile selection and leading `--`
are parsed before identifying the first command and action. Global overrides are
accepted but do not alter the bounded explanation's configured-value semantics.
Unknown/malformed prefixes, help/version and non-explain actions decline admission
and retain ordinary interrupted-pull recovery; payload adjacency never dispatches.
No additional checkout module or arbitrary plugin is imported to make that decision.
Bootstrap ordering is therefore an application-import boundary, not a ban on the
CLI's `sys` import or stdlib-only admission parsing. Ordinary startup still attempts
bootstrap before recovery/application imports, tolerates only an absent bootstrap,
and propagates missing dependencies of an existing bootstrap. Subprocess fixtures
must load the real `hermes_cli` package as console scripts do; a stripped main.py
without its package context is not a faithful startup probe. Read-only explanation
alone exits before even attempting bootstrap.
Profile extraction is a separate, literal pre-argparse pass (`-p NAME`,
`--profile NAME`, `--profile=NAME`), not an option in argparse's abbreviation
namespace. Thus `--pr=custom` and `--pro=custom` remain provider abbreviations.
A durable matrix generates every actual global long-option prefix and compares
accepted/rejected grammar, alongside whole-home/source snapshot regressions.

This source command is not installed merely by publishing its PR. Use the owning
installation's included updater after reviewing its entire target range and
runtime scope. Never copy these modules into a running source tree. Gateway
restart/reload and active-source mutation remain separately constrained by the
operator's instructions.
