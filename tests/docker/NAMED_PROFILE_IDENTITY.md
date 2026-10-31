# Named-profile shutdown identity regression

Scope: isolated follow-up to 07187eaeb78ac74b46b87099b5f133edefb96c6e.

Restart impact: restart_required if subsequently activated. Target processes:
container named-profile gateways only. Activation grouping: none authorized;
source preparation/testing only. Rollback identity: parent 07187eaeb78ac74b46b87099b5f133edefb96c6e.
No push, deployment, live configuration writes, or live restart.

Linux /proc/PID/environ exposes the initial exec environment. main.py's
post-exec HERMES_HOME profile resolution does not update that kernel view.
The supervised run script now exports the named profile home before exec,
retaining the configured custom root (including an inherited profile home).

Foreground `hermes -p NAME gateway run --no-supervise` retains its root home
in /proc. The shutdown helper accepts that precise argv shape only when the
initial root matches the state file's profile root. This is additional to
state kind, PID > 1, and matching kernel start ticks; PID alone is never enough.
The optional Python interpreter prefix is removed for console-script launches.
Other foreground argument shapes are intentionally not admitted by this fallback.

Behavioral regression: execute rendered launcher exports/exec in a disposable
network-disabled Docker container, replacing only fixed container cwd/uid setup.
The hermes executable imports real hermes_cli.main to exercise import-time
profile resolution, then remains alive without starting providers/platforms.
The actual helper reads real /proc and must terminate that verified owner.
Both supervised and foreground cases failed before the fix: helper returned
while the child remained alive. They pass after the fix. Custom-root and
inherited-profile-root startup are covered. Negative cases preserve unrelated
processes for wrong start ticks, kind, PID 1, profile, argv, and initial root.

Run these unittest files inside Docker (pytest's tests/docker/conftest.py skips
Docker tests when no nested Docker socket exists):

    PYTHONPATH=/candidate python -m unittest discover -s tests/docker -p test_named_gateway_identity.py -v
    PYTHONPATH=/candidate python -m unittest discover -s tests/docker -p test_s6_wait_contract.py -v

The whole-container test remains the real s6 shutdown budget regression:
`S6_TEST_OUTPUT=<new-directory> python3 tests/docker/test_s6_container_cleanup.py`.
