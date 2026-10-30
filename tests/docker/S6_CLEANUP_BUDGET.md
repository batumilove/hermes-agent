# Isolated s6 shutdown candidate

Base: b12617dcf42adef6a0fb1ced52f42bbbc8d8289c. Replaces rejected
62e338befe0090376968f9614e33520de7530946's unconditional final-sweep delay.
Restart impact: restart_required (new image only). Target: disposable Docker
containers. Activation: none; no live restart, deploy or push authorized.
Rollback identity: unchanged base image.

## Sequencing and limits

s6-overlay 3.2.3.0 runs legacy-cont-init's cont-finish hooks during the waited
s6-rc shutdown, before its final process sweep. The new hook runs as hermes
(the same UID as gateway; root without ptrace capability cannot read its
/proc environ). It reads gateway_state.json for the default home and profiles,
requires kind=hermes-gateway, matching Linux start ticks and process HERMES_HOME,
then sends TERM and polls only those owners. Zombies count as exited.
The wait returns as soon as owners exit, or at 80 seconds. A separate s6
maximumtime guard bounds the helper at 82 seconds. The global final sweep
retains its short default 3000ms. CMD dispatch and exit propagation are unchanged.
No shutdown marker or application state is written by the hook.

Both supported Compose gateway definitions explicitly set stop_grace_period:
90s. Plain Docker users should configure this when creating the container:

    docker run --stop-timeout 90 ... IMAGE gateway run
    docker stop CONTAINER

A shorter external deadline can still kill cleanup. Non-PID-1 fallback does
not have s6 hooks. No universal full-budget guarantee is claimed: configured
drains and process-tail operations may exceed these ceilings; existing gateway
aggregate/watchdog incomplete classification remains authoritative.

## Execution

    S6_TEST_OUTPUT=<new-scratch-dir> python3 tests/docker/test_s6_container_cleanup.py
    python3 tests/docker/test_s6_wait_contract.py

The harness overlays exact candidate source and hook wiring onto locally
available hermes-agent-harness:latest (5197f42f735e), real s6-overlay 3.2.3.0.
It is not a complete production Dockerfile rebuild. Network disabled, no host
bind mounts/socket/credentials. Uses documented Docker --stop-timeout 90 at
creation and plain docker stop, not a hidden timeout override at stop.

The probe exercises real GatewayRunner cleanup with 0s, 5s, and 120s finalize
sleeps. The last exhausts the aggregate cleanup near 50s and must not create a
clean marker. The stuck probe SIGSTOPs itself after drain (freezing watchdog
threads too), genuinely requiring the s6 ceiling/final SIGKILL fallback.

Historical evidence remains in s6-budget-{red,final}-evidence. Remediation
first-run evidence in s6-wait-r1-evidence exposed root /proc environ access;
second run s6-wait-r2-evidence showed the thread watchdog defeating an event-loop
only stuck injection. Those are preserved, not presented as passing runs.

Known baseline harness warnings include stale optional Spotify/flux3 imports
and hosted-room-worker Path NameError. These do not prevent startup or the
shutdown paths under test and are not broad-suite success claims.

Final passing receipts: `/home/ubuntu/.hermes/cache/scratch/s6-wait-verified-evidence/results.json`:
fast 3.788s, 5s cleanup 8.621s (both DB close/stopped/clean), aggregate-incomplete
53.814s (DB close/stopped, no clean), SIGSTOP-stuck 83.331s (no clean, wait-exhausted
breadcrumb). Bare executable `sh -c 'exit 23'` preserved status 23 in 5.196s.
Host Docker Compose plugin is absent, so Compose definitions were source-checked,
not executed; real tests use documented Docker creation-time stop timeout.

Independent exact-candidate review is required before promotion. No production
acceptance or live slow-path verification is claimed.
