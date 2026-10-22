# Authentic worker policy fixture

`skills.zip` contains the complete two skill directories from
[`batumilove/hermes-skills` at `9da856dcda82dc353af53f91e3ed0b49932fc476`](https://github.com/batumilove/hermes-skills/tree/9da856dcda82dc353af53f91e3ed0b49932fc476):

- `devops/kanban-worker`
- `software-development/restart-window-bundling`

The original policies, references, validator, validator self-tests and manifest
template retain their exact Git blob bytes. Both original `SKILL.md` files
declare `license: MIT`; those declarations remain unchanged. The source
repository has no separate license file. `SOURCE.json` records the exact commit,
tree, original blob IDs, byte counts and SHA256 for all thirteen members.

The fixture exists because upstream real-process E2E homes do not install the
fork's external skill component. The maintained dispatcher requires both
policies, including for freshly installed and updated worker interpreters.
Tests provision them into isolated homes before dispatch; a missing or changed
fixture fails before any policy is written. Nothing activates a profile, starts
a service, runs an installer or bypasses mandatory skill loading.

The ZIP is a public source fixture, with sorted members, a fixed 1980 timestamp,
regular 0644 permissions and deflate level9. Its digest and the source manifest's
digest are independently pinned by the provisioner. Normal archive/blob guards
apply without an exception. The installed component pin remains owned by the
repository's component lock and installer.
