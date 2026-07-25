# Docker Compose CD for the Batumi Hermes fork

This document describes the fork-owned deployment path for
`batumilove/hermes-agent`. It does not change or weaken the upstream
`NousResearch/hermes-agent` Docker Hub release boundary. Fork deployment images
use the separate repository `ghcr.io/batumilove/hermes-agent-deploy`.

## Delivery model

1. A merge lands on `batumi/live`.
2. `.github/workflows/deploy-compose.yml` waits for the exact commit's
   `All required checks pass` result from the GitHub Actions app.
3. The workflow refuses to continue if `batumi/live` has moved to a newer SHA.
4. The workflow resolves the commit tag before building. A verified existing
   digest is reused; only a definitive registry 404 permits BuildKit to publish
   a Linux/amd64 image with SBOM and provenance. The commit tag and `candidate`
   tag are discovery aids only; deployment always uses the returned `sha256:`
   digest.
5. The digest is deployed automatically to the `batumi-staging` GitHub
   environment.
6. After staging validation, `.github/workflows/promote-compose.yml` promotes
   the same digest to `batumi-production`; it never rebuilds the image.
7. The host deploy helper serializes deployments with `flock`, pulls before
   replacing the healthy container, waits for the s6-supervised gateway health
   check, and restores the prior digest on failure.

`deploy/compose.yml` intentionally runs only the gateway. Add the dashboard as
a separately reviewed service after gateway migration is stable. Both staging
and production use host networking, so they must be on separate hosts and must
use separate Hermes homes and messaging credentials.

## GitHub environments

Create `batumi-staging` and `batumi-production`. Configure these environment
variables in each environment:

| Variable | Example | Purpose |
| --- | --- | --- |
| `DEPLOY_HOST` | `hermes-staging-01` | Tailnet DNS name; no shell syntax |
| `DEPLOY_USER` | `hermes-deploy` | Dedicated deployment account |
| `DEPLOY_ROOT` | `/opt/hermes-compose/staging` | Account-owned deployment root |
| `TAILSCALE_TAGS` | `tag:ci` | Ephemeral runner identity |

Configure these environment secrets:

- `TAILSCALE_OAUTH_CLIENT_ID` and `TAILSCALE_OAUTH_SECRET` (preferred), or
  `TAILSCALE_AUTHKEY` as a scoped, reusable, ephemeral-key fallback
- `DEPLOY_SSH_KEY`
- `DEPLOY_KNOWN_HOSTS`

Automatic staging is additionally gated by the repository Actions variable
`HERMES_STAGING_DEPLOY_ENABLED`. Keep it set to `false` until the staging host
preflight and first manual deployment have passed; set it to `true` only after
that evidence exists. Image publication can continue while deployment remains
disabled.

Use a Tailscale OAuth client restricted to creation of the CI tag. ACLs should
allow that tag to reach only TCP/22 on the two deployment hosts. The SSH key
should belong only to the unprivileged deployment account. Docker-group access
is already root-equivalent; do not also grant broad passwordless sudo.

If the auth-key fallback is used, create it with only `tag:ci`, `ephemeral`,
`preauthorized`, and the shortest practical expiry. Record its expiry and
rotate it before expiration; never use a broad personal auth key.
The workflow uses strict host-key checking. Store an exact, independently
verified ED25519 known-host entry; do not use `ssh-keyscan` during deployment.

Production should require an environment approval. For a personal repository,
configure `batumilove` as reviewer with self-review allowed, or use a second
maintainer when available. Staging should not require approval but should allow
only `batumi/live`.

> `workflow_dispatch` workflows are exposed from the repository's default
> branch. This fork uses protected `batumi/live` as its default, so manual
> promotion and rollback use the same reviewed deployment code as automatic
> staging.

## Publication idempotency

The immutable source tag is `sha-<40-character source SHA>`. Before building,
`scripts/deploy/resolve_ghcr_digest.py` obtains a scoped GHCR pull token and
performs an authenticated manifest request:

- `404` with registry error code `MANIFEST_UNKNOWN`: the source tag does not
  exist and this run may build it. Generic or authorization-masked 404s fail.
- `200`: the workflow reuses `Docker-Content-Digest` only after
  authenticated `gh attestation verify` constrains the certificate source SHA,
  source ref, signer workflow, signer digest, and hosted runner. The SLSA
  statement must additionally bind that exact subject digest to the expected
  Git repository/ref URI and commit.
- Any other registry response, malformed digest, token error, or provenance
  mismatch: fail closed without building or deploying.

This prevents workflow reruns from replacing the canonical source tag with a
different rebuild. The mutable `candidate` tag is not a deployment input.

## Host preparation

The workflow does not bootstrap Docker, modify firewall policy, create users,
or install registry credentials. Those are infrastructure operations and must
be reviewed independently.

Required host state:

1. Docker Engine and Docker Compose v2 are installed.
2. The deployment account can run Docker and owns `DEPLOY_ROOT`. Membership in
   the Docker group is root-equivalent; restrict SSH and Tailscale access
   accordingly.
3. The host is already authenticated for pull-only access to
   `ghcr.io/batumilove/hermes-agent-deploy`, or the package is deliberately
   public. Never pass a registry token through workflow command arguments.
4. A dedicated Hermes data directory exists and is backed up off-host.
5. Runtime secrets are present only inside that Hermes home or its approved
   host-side secret injection path. They do not belong in Compose or GitHub
   workflow files.
6. The deployment account's `runtime.env` exists at mode `0600`.

Example staging preparation (values are examples, not a command to run on
production):

```bash
install -d -m 0700 /opt/hermes-compose/staging
install -d -m 0700 /home/hermes-staging/.hermes-staging
cat >/opt/hermes-compose/staging/runtime.env <<'EOF'
HERMES_DATA_DIR=/home/hermes-staging/.hermes-staging
HERMES_UID=1001
HERMES_GID=1001
EOF
chmod 0600 /opt/hermes-compose/staging/runtime.env
```

`HERMES_UID` and `HERMES_GID` must match the owner of `HERMES_DATA_DIR`.
Create `runtime.env` directly on the host; do not generate it in Actions if it
contains site-specific or sensitive values.

The container root filesystem is not mounted globally read-only. Hermes' s6
initialization must update the internal user/group records before dropping
privileges to the host-matching UID/GID. The application tree remains
root-owned and non-writable to the runtime Hermes user, and Compose enables
`no-new-privileges`; forcing `read_only: true` would break the supported UID/GID
remap rather than provide a usable hardening layer.

Likewise, `/run` must explicitly use the `exec` mount flag: Docker's tmpfs
default includes `noexec`, while s6-overlay copies and executes its init from
`/run/s6`. `/run` remains an isolated `nosuid,nodev` tmpfs; `/tmp` remains
`noexec,nosuid,nodev`.

## Staging target

The existing isolated target is:

- host: `hermes-staging-01`
- VMID: `429`
- Hermes home: `/home/hermes-staging/.hermes-staging`
- existing profiles: `gateway-canary`, `skill-lab`

VM 429 is prepared with Docker Compose v2, the unprivileged `hermes-deploy`
account, strict SSH host-key verification, and staging-only persistent state.
The first digest deployment and rollback were observed successfully before
automatic staging was enabled. Never mount `/home/ubuntu/.hermes` or use the
production Telegram token on staging.

## Production migration safety

Production currently runs `hermes-gateway.service` as a source installation on
`hermes-vm`. Do not run the containerized gateway concurrently with that unit:
both processes would compete for the same Telegram token and state files.

A production cutover requires a separate approved maintenance window:

1. Verify a current off-host backup and a tested restore path for
   `/home/ubuntu/.hermes`.
2. Record the running source commit and systemd unit state.
3. Verify the chosen digest has passed staging using non-production credentials.
4. Pull the digest before stopping the systemd gateway.
5. Stop and disable only the confirmed production gateway unit.
6. Start the Compose release and verify container health, Telegram ownership,
   dashboard/API exposure, cron state, and provider access.
7. If verification fails, stop Compose, restore the prior release/state if
   needed, and restart the original systemd unit.

Do not automate that first cutover. After one successful observed migration,
normal production promotions may use the environment-gated workflow.

## Promotion and rollback

Promotion requires a digest and its source SHA. The workflow requires the
source commit to be contained in protected `batumi/live`, verifies that the
digest exists in the fork GHCR repository, then deploys that same digest:

```text
environment: batumi-production
operation: deploy
image_digest: sha256:<64 hex characters>
source_sha: <40-character commit SHA>
```

Rollback uses the host's `release.previous.env` and needs no registry tag:

```text
environment: batumi-production
operation: rollback
source_sha: <40-character incident/change SHA>
```

The host records timestamp, result, environment, source SHA, and digest in
`DEPLOY_ROOT/releases/history.tsv` with mode `0600`. It never records secret
values or container logs.

## Failure behavior

- CI missing, pending, failed, cancelled, or attached to another SHA: no image.
- Branch advanced while waiting: stale deployment exits.
- Existing-tag resolution error or provenance mismatch: no build and no
  deployment.
- Build or attestation failure: no deployment.
- Tailscale, SSH, host-key, Compose preflight, or registry pull failure: the
  currently healthy release remains running.
- New container fails health: previous digest is restored automatically.
- Automatic rollback also fails: workflow fails loudly for operator action.
- First deployment fails: unhealthy gateway is stopped.

The workflow concurrency group serializes publication runs without cancellation,
preventing two reruns from racing to publish the same source tag. Queued stale
runs fail the protected-branch SHA check. The host deployment lock separately
prevents overlapping remote changes.

## Staging socket diagnostic transaction helper

The staging-only socket diagnostic workflow delegates the privileged transaction
to `/usr/local/libexec/hermes-staging-diagnostic`. GitHub can send only a UTF-8
JSON request of at most 4096 bytes on stdin to the exact no-argument sudo
command. Paths, host, environment, container, image, commands, recovery mode,
and duration choices are fixed in the root-owned helper. Durable transaction
state is root-owned under `/var/lib/hermes-staging-diagnostics`; interrupted or
ambiguous transactions are restore-only and never resume diagnostics.

Rollout is intentionally split and fail-closed:

1. Never execute the checkout installer as root. Export
   `scripts/deploy/install-staging-diagnostic-helper.sh` from the exact reviewed
   commit, verify its externally reviewed SHA-256 while unprivileged, copy it to
   `/usr/local/sbin/hermes-staging-diagnostic-installer` as `root:root 0755`, and
   read back that root-owned copy's SHA-256 **before first execution**. Any
   mismatch is a hard stop.
2. Run the verified root-owned copy as
   `--stage REPO_ROOT COMMIT TREE INSTALLER_SHA256` against the clean exact
   reviewed checkout. The installer refuses execution from any other path and
   self-verifies its owner, mode, digest, and reviewed Git object. Staging verifies every artifact
   against the named Git object before installing it, writes a root-owned
   commit/tree/hash manifest, and installs the tmpfiles rule that recreates the
   shared deployment/recovery lock after reboot. Sudo authorization and timer
   activation remain disabled.
3. Read back the manifest, installer/helper/unit/tmpfiles hashes and ownership/modes,
   shared-lock metadata, and state-directory mode. Verify every value against
   the reviewed commit/tree.
4. Run the separate `--authorize` phase. It substitutes the verified SHA-256
   into the exact no-argument sudo rule and validates it with `visudo -c -f`.
5. Run a no-mutation invalid-request canary, then crash canaries at each durable
   boundary. Verify byte-for-byte config restoration, healthy runtime, and
   effective `socket_diagnostics=false` after every case.
6. Enable the recovery timer only through a separate approved host operation.
7. Run one 60-second live gate and verify only bounded aggregate evidence is
   returned. Activate the workflow only after all earlier gates pass by setting
   `HERMES_STAGING_DIAGNOSTICS_ENABLED=true`; each dispatch must separately set
   `activation_ack=enabled` and remain bound to the exact deployed source SHA.

**Security boundary:** this correctness helper does not create least privilege.
`hermes-deploy` Docker-group membership remains root-equivalent, so privilege
containment remains **FAIL** until a separately reviewed deployment-helper
migration removes Docker access. This candidate deliberately does not change
users or groups.

Rollback order: revoke the diagnostic sudoers rule first; preserve recovery
until every transaction is `RESTORED` or safely `ABORTED`; force and verify exact restoration; revert
the workflow; then disable/remove the timer, service, and helper. Never restore
Docker-group membership as a rollback convenience.

## Staging provider-telemetry promotion

`.github/workflows/staging-provider-telemetry-deploy.yml` is the only supported
automated path for promoting the private `infra-ops` provider-telemetry
integration. It is manual, staging-only, serialized, attached to the
`batumi-staging` environment, and disabled unless all of these environment
variables are configured:

- `HERMES_STAGING_TELEMETRY_DEPLOY_ENABLED=true`
- `HERMES_STAGING_TELEMETRY_APPROVED_SHA=<exact reviewed infra-ops commit>`
- `HERMES_STAGING_TELEMETRY_APPROVED_TREE=<exact reviewed infra-ops tree>`

Each dispatch must provide the same exact commit/tree, the exact Hermes source
SHA already running in staging, and `activation_ack=enabled`. The workflow
checks out the private source with a fine-grained `INFRA_OPS_READ_TOKEN` that
has read-only Contents access to `batumilove/infra-ops`; do not substitute a
classic or broad personal token.

Required `batumi-staging` secrets:

- `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS` for
  `hermes-deploy@hermes-staging-01`
- `MONITORING_DEPLOY_SSH_KEY` and `MONITORING_KNOWN_HOSTS` for
  `ubuntu@monitoring-vm`
- `INFRA_OPS_READ_TOKEN`
- the existing scoped Tailscale credential

The transaction verifies the private Git commit and tree, creates a manifest
for exactly two plugin files and one Prometheus rule file, and transfers only
those files. On the Hermes host it verifies the running source SHA and fixed
`/opt/data` bind mount, captures all existing counters, stops only the main
Hermes service under s6, swaps the plugin directory, restarts, and requires:

- healthy gateway and `hermes_provider_telemetry_up 1`
- exact installed plugin hashes
- a live PID holding the mode-`0600` writer lock
- no disappeared or decreased persisted counter series

On `monitoring-vm`, the candidate is checked with the live container's
`promtool` before atomic installation, Prometheus is reloaded, and the rules
API must expose exactly one `HermesProviderTelemetryCounterRegression` rule.
Backups live outside the active rule glob. Any later-stage failure rolls back
the earlier stage; a failed rollback exits distinctly with
`ROLLBACK_FAILED` and requires manual recovery. Preserve transaction backups
until the observation window closes.

**Security boundary:** the staging identity remains Docker-group/root-equivalent
and the monitoring identity uses passwordless sudo. Environment approval,
immutable source checks, strict host-key verification, fixed destinations, and
reviewed scripts constrain the workflow but do not turn those credentials into
least-privilege identities. A future helper/forced-command migration is a
separate hardening change.
