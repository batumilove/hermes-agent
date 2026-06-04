# Context-route gateway canary runbook

Status: operational runbook after PASS re-review
Scope: Hermes Agent `context_efficiency` advisor/outcome telemetry only
Repo: `/home/ubuntu/.hermes/hermes-agent`
Gate: re-review `t_3bf9f819` PASS supersedes the initial blocked parent review. Do not use this runbook until that PASS remains the active gate.

## Safety invariants

- Use an isolated canary profile/gateway route only. Do not modify default/live Telegram routing.
- Keep telemetry observational only: no adaptive routing, no toolset/schema changes, no prompt/system-prompt mutation, no blocking, rerouting, retries, or auto tool calls based on advisor output.
- Keep the default/live profile disabled for `context_efficiency` until a separate explicit promotion change is approved.
- Write canary telemetry only under the canary profile home unless a reviewed absolute canary path is intentionally configured.
- Treat `arg_preview` and `result_preview` as sensitive bounded diagnostics. They are optional and should stay disabled unless the specific canary needs them for a reviewed privacy spot check.

## 0. Shell setup

```bash
cd /home/ubuntu/.hermes/hermes-agent
export CANARY_PROFILE=tdai-canary
export CANARY_HOME=/home/ubuntu/.hermes/profiles/$CANARY_PROFILE
export CANARY_LOG=$CANARY_HOME/logs/context_efficiency-canary.jsonl
export OUTCOME_DIR=$CANARY_HOME/runs/context-route
mkdir -p "$CANARY_HOME/logs" "$OUTCOME_DIR"
```

## 1. Pre-flight verification

Verify the repo and preserve unrelated work before making any changes:

```bash
git status --short --branch
git remote -v
git log --oneline --decorate -8
```

Verify `tdai-canary` has telemetry enabled and the expected canary log path:

```bash
python - <<'PY'
from pathlib import Path
import sys
try:
    import yaml
except Exception as exc:
    raise SystemExit(f"PyYAML is required for this check: {exc}")

path = Path('/home/ubuntu/.hermes/profiles/tdai-canary/config.yaml')
cfg = yaml.safe_load(path.read_text()) or {}
ce = cfg.get('context_efficiency') or {}
print({'profile': 'tdai-canary', 'enabled': ce.get('enabled'), 'log_path': ce.get('log_path'), 'previews_enabled': ce.get('previews_enabled', False)})
assert ce.get('enabled') is True
assert ce.get('log_path') == 'logs/context_efficiency-canary.jsonl'
PY
```

Verify the default/live profile remains disabled. Absence of a `context_efficiency` section in `/home/ubuntu/.hermes/config.yaml` is acceptable because Hermes defaults `enabled` to false in code.

```bash
python - <<'PY'
from pathlib import Path
import yaml
from hermes_cli.config import DEFAULT_CONFIG

paths = [
    Path('/home/ubuntu/.hermes/config.yaml'),
    Path('/home/ubuntu/.hermes/profiles/repo-ops/config.yaml'),
]
for path in paths:
    cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
    ce = (cfg or {}).get('context_efficiency') or {}
    effective_enabled = ce.get('enabled', DEFAULT_CONFIG['context_efficiency']['enabled'])
    print({'path': str(path), 'configured': bool(ce), 'effective_enabled': effective_enabled})
    assert effective_enabled is False
print({'default_config_enabled': DEFAULT_CONFIG['context_efficiency']['enabled']})
assert DEFAULT_CONFIG['context_efficiency']['enabled'] is False
PY
```

If either check fails, stop and rollback/repair config before running any canary traffic.

## 2. Focused verification before canary traffic

Run the focused tests and compile checks that cover telemetry/reporting/outcome tooling:

```bash
python -m pytest \
  tests/agent/test_context_efficiency.py \
  tests/agent/test_context_efficiency_report.py \
  tests/agent/test_context_route_outcome_report.py \
  tests/scripts/test_context_efficiency_canary_batch.py \
  -q

python -m py_compile \
  agent/context_efficiency.py \
  agent/context_efficiency_report.py \
  agent/context_route_outcome_report.py \
  scripts/context_efficiency_canary_batch.py \
  scripts/context_efficiency_report.py \
  scripts/context_route_outcome_report.py \
  hermes_cli/config.py
```

Pass condition: all commands exit 0. If broad unrelated repo changes exist, do not include them in this canary decision.

## 3. CLI outcome batch

First run a dry-run to confirm the selected cases and output paths without invoking model/tool traffic:

```bash
python scripts/context_efficiency_canary_batch.py \
  --profile "$CANARY_PROFILE" \
  --natural \
  --repeat 1 \
  --dry-run
```

Run the real CLI outcome batch against the isolated canary profile. Use the fixed prompt set when available; otherwise use the reviewed natural cases and record that this is a smoke batch, not a full promotion eval.

```bash
rm -f "$CANARY_LOG"
python scripts/context_efficiency_canary_batch.py \
  --profile "$CANARY_PROFILE" \
  --natural \
  --repeat 3 \
  --write-run-summary
```

Expected artifacts:

- telemetry JSONL: `$CANARY_LOG`
- auto-written run summary: `$OUTCOME_DIR/<timestamp>.json`

Verify telemetry stayed in the canary home and was not written under the default profile:

```bash
test -s "$CANARY_LOG"
test ! -s /home/ubuntu/.hermes/logs/context_efficiency-canary.jsonl
find "$OUTCOME_DIR" -maxdepth 1 -type f -name '*.json' -print | sort
```

## 4. Outcome report

Generate the outcome report from the batch summary artifacts. The report command must fail if the input glob is wrong or empty; do not accept an empty-success report.

```bash
RUN_SUMMARIES=("$OUTCOME_DIR"/*.json)
test -e "${RUN_SUMMARIES[0]}"
python scripts/context_route_outcome_report.py "${RUN_SUMMARIES[@]}"
python scripts/context_route_outcome_report.py "${RUN_SUMMARIES[@]}" --json \
  > "$OUTCOME_DIR/outcome-report.json"
python - <<'PY'
from pathlib import Path
import json, os
report = json.loads(Path(os.environ['OUTCOME_DIR'], 'outcome-report.json').read_text())
print(json.dumps({
    'runs': report.get('run_count'),
    'cases': report.get('case_count'),
    'mismatch_events': report.get('mismatch_event_count'),
}, indent=2))
assert report.get('run_count', 0) > 0
PY
```

Also generate a telemetry-level route summary:

```bash
python scripts/context_efficiency_report.py "$CANARY_LOG" --limit 200 \
  > "$OUTCOME_DIR/context-efficiency-report.txt"
```

Privacy spot check the canary log:

```bash
python - <<'PY'
from pathlib import Path
import os, re, sys
log = Path(os.environ['CANARY_LOG'])
text = log.read_text(errors='ignore') if log.exists() else ''
patterns = [
    r'(?i)api[_-]?key', r'(?i)authorization', r'(?i)bearer\s+[A-Za-z0-9._-]+',
    r'(?i)cookie', r'(?i)client_secret', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
]
hits = [pat for pat in patterns if re.search(pat, text)]
print({'log': str(log), 'bytes': len(text), 'potential_secret_patterns': hits})
if hits:
    sys.exit(2)
PY
```

Pass condition:

- outcome report inputs are non-empty and report commands exit 0;
- outcome-report mismatch counts/details are reviewed and triaged;
- no secret/privacy pattern appears in canary telemetry;
- default/live profile still has `context_efficiency.enabled=false`.

## 5. Separate gateway/canary route smoke

Only run this after the CLI outcome batch and reports pass. Use a canary gateway route/profile, private test bot/chat/topic, or isolated canary container. Do not point the default live Telegram bot/profile at this traffic.

Pre-start checks:

```bash
hermes --profile "$CANARY_PROFILE" config set context_efficiency.enabled true
hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path logs/context_efficiency-canary.jsonl
hermes --profile "$CANARY_PROFILE" config set context_efficiency.previews_enabled false
hermes --profile "$CANARY_PROFILE" gateway status || true
```

Start a canary gateway only according to the isolated deployment plan. Example foreground smoke for a canary platform config:

```bash
HERMES_PROFILE="$CANARY_PROFILE" hermes --profile "$CANARY_PROFILE" gateway run
```

From the private canary chat/topic, send three synthetic prompts that exercise distinct route families:

1. `Where did we leave the last Hermes gateway routing discussion?`
2. `Find where context_efficiency defaults are defined in this repo.`
3. `Search the web for the current Hermes Agent docs URL.`

During/after the smoke, verify:

```bash
tail -n 80 "$CANARY_HOME/logs/gateway.log" 2>/dev/null || true
test -s "$CANARY_LOG"
python scripts/context_efficiency_report.py "$CANARY_LOG" --limit 200
python - <<'PY'
from pathlib import Path
import json, os
log = Path(os.environ['CANARY_LOG'])
for line in log.read_text().splitlines()[-50:]:
    ev = json.loads(line)
    assert 'prompt_excerpt' not in ev and 'prompt_preview' not in ev
    assert 'args_excerpt' not in ev and 'result_excerpt' not in ev
print('gateway canary telemetry field shape ok')
PY
```

Gateway smoke pass condition:

- canary gateway replies normally;
- no duplicate/missing final replies, topic leakage, approval wedge, or gateway crash;
- no telemetry-induced exceptions in canary gateway logs;
- telemetry appears in `$CANARY_LOG` and not under `/home/ubuntu/.hermes/logs/`;
- default/live Telegram routing was not changed.

## 6. Rollback

Rollback disables the canary and restarts/resets only the canary process. Preserve logs/reports for analysis unless they contain sensitive data.

```bash
hermes --profile "$CANARY_PROFILE" config set context_efficiency.enabled false
hermes --profile "$CANARY_PROFILE" config set context_efficiency.previews_enabled false
hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path logs/context_efficiency.disabled.jsonl
hermes --profile "$CANARY_PROFILE" gateway restart || true
hermes --profile "$CANARY_PROFILE" gateway status || true
```

If the canary gateway is a foreground/manual process, stop that canary process instead of restarting the live gateway. If it is systemd-managed, reset only the canary service, for example:

```bash
systemctl --user reset-failed hermes-gateway-tdai-canary.service || true
systemctl --user restart hermes-gateway-tdai-canary.service
```

Re-verify default/live remains disabled:

```bash
python - <<'PY'
from pathlib import Path
import yaml
from hermes_cli.config import DEFAULT_CONFIG
path = Path('/home/ubuntu/.hermes/config.yaml')
cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
ce = (cfg or {}).get('context_efficiency') or {}
assert ce.get('enabled', DEFAULT_CONFIG['context_efficiency']['enabled']) is False
print('default/live context_efficiency remains disabled')
PY
```

Immediate rollback triggers:

- any secret/token/private transcript/full memory leak in telemetry;
- any behavior change attributable to telemetry;
- any gateway delivery/routing regression;
- telemetry errors that propagate into user responses or tool execution;
- report parser errors on valid log events;
- operator confusion between canary and live profiles.

## 7. Log retention and cleanup

Default retention for successful canary artifacts: keep the latest 7 days or latest 10 batch runs, whichever is smaller, unless a promotion review explicitly pins a report.

Artifacts:

- telemetry: `$CANARY_HOME/logs/context_efficiency-canary.jsonl`
- run summaries/reports: `$CANARY_HOME/runs/context-route/*.json`
- mismatch text reports: `$CANARY_HOME/runs/context-route/*.txt`

Archive useful reports before cleanup:

```bash
tar -C "$CANARY_HOME" -czf "$CANARY_HOME/runs/context-route-$(date +%Y%m%d-%H%M%S).tgz" \
  logs/context_efficiency-canary.jsonl runs/context-route
```

Cleanup unpinned logs/reports:

```bash
find "$CANARY_HOME/runs/context-route" -type f \( -name '*.json' -o -name '*.txt' \) -mtime +7 -print
find "$CANARY_HOME/runs/context-route" -type f \( -name '*.json' -o -name '*.txt' \) -mtime +7 -delete
: > "$CANARY_LOG"
```

If a privacy failure is found, do not archive the raw JSONL. Move it to a restricted incident path or delete it after extracting only non-sensitive aggregate counts needed for the incident note.

## 8. Handoff checklist

A canary run is ready for review only when the handoff includes:

- git head and dirty-state summary;
- pre-flight config proof: `tdai-canary` enabled, default/live disabled;
- focused test and py_compile output;
- CLI batch command and artifact paths;
- outcome report and telemetry report paths;
- gateway smoke target description without secrets/tokens;
- privacy spot-check result;
- rollback command used or ready;
- confirmation that default/live Telegram routing was not modified.
