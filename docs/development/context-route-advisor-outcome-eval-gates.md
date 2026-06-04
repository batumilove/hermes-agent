# Context-route advisor outcome-eval gates

Status: draft acceptance and promotion spec
Scope: Hermes Agent context_efficiency/context-route advisor telemetry only

## Purpose

This document defines the gates required before the context-route advisor is considered safe to promote beyond isolated Hermes canaries. The advisor is currently an observational signal: it records what context route a user prompt appears to need and compares that with the context tools actually used. Promotion means broader telemetry/canary exposure only. It does not mean adaptive routing.

Hard invariant: context_efficiency must remain disabled in the default/live profile until every gate below has passed and a separate explicit promotion change is approved.

## Non-goals

The canary must not:

- change model selection, provider selection, tool schemas, enabled toolsets, system prompts, memory injection, compression behavior, or gateway routing;
- block, reroute, retry, or auto-call tools based on advisor output;
- write user prompt text, tool arguments, tool results, secrets, or raw memory/session contents beyond the configured bounded telemetry snippets;
- tune itself online or update config from telemetry;
- decide whether a response is correct by model judgment alone;
- replace human review for privacy, gateway behavior, or live-profile promotion.

## Gate 1: privacy and logging

Acceptance criteria:

1. Observational-only behavior is verified from code and tests: `record_tool_route(...)` must swallow telemetry failures and never affect tool execution.
2. Telemetry writes only to the configured `context_efficiency.log_path` under the active `HERMES_HOME` unless an absolute canary path is explicitly configured.
3. Logged fields are bounded and scrubbed enough for local analysis:
   - `prompt_excerpt`, `args_excerpt`, and `result_excerpt` are length-limited by config.
   - no API keys, bearer tokens, cookies, authorization headers, private keys, or credential file contents appear in sampled events.
   - no full session transcript, full memory profile, or full tool result is logged.
4. Telemetry can be deleted by removing the canary JSONL file; no other persistent state is required for the report.
5. The report command can run without network access.

Suggested verification commands:

```bash
cd /home/ubuntu/.hermes/hermes-agent
python -m py_compile agent/context_efficiency.py agent/context_efficiency_report.py
python -m pytest tests/agent/test_context_efficiency.py tests/agent/test_context_efficiency_report.py -q
```

Manual privacy spot check after a canary run:

```bash
CANARY_HOME="$HOME/.hermes/profiles/context-route-canary"
LOG="$CANARY_HOME/logs/context_efficiency.jsonl"
python -m agent.context_efficiency_report "$LOG" --limit 200
python - <<'PY'
from pathlib import Path
import re, os
log = Path(os.environ.get('LOG', Path.home() / '.hermes/profiles/context-route-canary/logs/context_efficiency.jsonl'))
text = log.read_text(errors='ignore') if log.exists() else ''
patterns = [
    r'(?i)api[_-]?key', r'(?i)authorization', r'(?i)bearer\s+[A-Za-z0-9._-]+',
    r'(?i)cookie', r'(?i)client_secret', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
]
for pat in patterns:
    if re.search(pat, text):
        print(f'POTENTIAL_SECRET_PATTERN {pat}')
PY
```

Pass condition: the tests pass, the sampled report is readable, and any secret-pattern hit is explained as field-name prose rather than a secret value. Any actual secret or sensitive full-content leak is an immediate rollback/fail.

## Gate 2: 30-50 prompt outcome-eval set

Build a fixed local eval set of 30-50 prompts before judging the advisor. Store the prompt set outside telemetry logs, for example under `evals/context_route_advisor/prompts.yaml`, and commit it only after reviewing that it contains no secrets or private raw user content.

Required shape:

```yaml
- id: session-001
  prompt: "What did we decide last time about the gateway restart flow?"
  expected_family: session_search
  allowed_families: [session_search]
  must_not_use: [web_research]
  notes: "Past conversation recall."
- id: repo-001
  prompt: "Find where the Telegram callback query handler is registered in this repo."
  expected_family: repo_files
  allowed_families: [repo_files]
  must_not_use: [durable_memory, web_research]
  notes: "Local source inspection."
```

Minimum coverage:

- 8-10 past-session/session_search prompts.
- 8-10 durable-memory/Honcho/user-profile prompts.
- 8-10 local repo/file prompts.
- 5-8 current public web/research prompts.
- 5-8 no-context/simple prompts where no context route should be recommended or used.
- At least 5 ambiguous prompts with explicitly allowed alternate families.
- At least 5 negative controls where a tempting but wrong route is listed in `must_not_use`.

Outcome labels:

- `expected_family`: primary target family.
- `allowed_families`: acceptable alternatives for ambiguous prompts.
- `must_not_use`: routes/families that indicate a bad recommendation or bad actual behavior.
- `notes`: one-line rationale.

Pass condition on the fixed set:

- At least 85% of advisor recommendations are in `allowed_families`.
- 0 critical misroutes: no recommendation in `must_not_use` for privacy-sensitive prompts, no web recommendation for private/local-only prompts, and no durable-memory recommendation for pure public web prompts.
- At least 90% of actual tool families are explainable by the prompt label or task execution trace.
- Every mismatch in the report is triaged as: advisor bug, prompt ambiguity, tool execution choice, or label error.

## Gate 3: repeated-run stability

Run the same 30-50 prompt set repeatedly before promotion.

Minimum protocol:

- 3 full runs on the same model/provider/config.
- 1 full run after a fresh process start.
- 1 full run with the same canary profile but a clean telemetry log.

Stability metrics:

- Advisor family agreement for each prompt is stable in at least 90% of prompts across all runs.
- No prompt flips into a `must_not_use` family in any run.
- Aggregate mismatch rate varies by no more than 10 percentage points between runs.
- Error rate in telemetry/report parsing is 0.

Repeated-run commands should write separate logs so diffs are easy:

```bash
cd /home/ubuntu/.hermes/hermes-agent
CANARY_PROFILE=context-route-canary
for run in 1 2 3; do
  hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path "logs/context_efficiency-run-$run.jsonl"
  # Execute the fixed prompt set with the chosen eval harness here.
  python -m agent.context_efficiency_report "$HOME/.hermes/profiles/$CANARY_PROFILE/logs/context_efficiency-run-$run.jsonl" --json \
    > "/tmp/context-efficiency-run-$run.json"
done
```

Pass condition: stability metrics pass and all mismatches have written triage notes.

## Gate 4: CLI canary

Use an isolated profile. Do not modify the default or live gateway profile.

Setup:

```bash
cd /home/ubuntu/.hermes/hermes-agent
CANARY_PROFILE=context-route-canary
hermes profile create "$CANARY_PROFILE" --clone repo-pm
hermes --profile "$CANARY_PROFILE" config set context_efficiency.enabled true
hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path logs/context_efficiency.jsonl
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_arg_chars 500
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_result_chars 500
```

Run representative prompts manually or through the eval harness:

```bash
hermes --profile "$CANARY_PROFILE" chat -q "Where did we leave the last Hermes gateway routing discussion?"
hermes --profile "$CANARY_PROFILE" chat -q "Find the context_efficiency config defaults in this repo."
hermes --profile "$CANARY_PROFILE" chat -q "Search the web for the current Hermes Agent docs URL."
```

Generate reports:

```bash
LOG="$HOME/.hermes/profiles/$CANARY_PROFILE/logs/context_efficiency.jsonl"
python -m agent.context_efficiency_report "$LOG"
python -m agent.context_efficiency_report "$LOG" --mismatches-only
python -m agent.context_efficiency_report "$LOG" --json > /tmp/context-efficiency-report.json
python -m agent.context_efficiency_report "$LOG" --family session_search
```

CLI canary pass condition:

- Hermes answers normally; no prompt is blocked or rerouted by the advisor.
- Telemetry appears only in the canary profile log.
- Report commands produce both human-readable and JSON output.
- No secret/privacy failure is found in the log spot check.
- The fixed outcome-eval set meets Gate 2 and Gate 3 thresholds.

## Gate 5: gateway canary

Gateway canary is allowed only after the CLI canary passes. It must use an isolated profile/container/session and a non-default gateway target. Prefer a private test bot/chat/topic or a canary platform account.

Gateway setup criteria:

- `context_efficiency.enabled=true` only in the canary gateway profile.
- The default/live profile remains disabled.
- Canary log path is profile-local and easy to delete.
- Canary gateway is labeled in status/logs so operators can distinguish it from live.
- Test traffic uses synthetic prompts or reviewed eval prompts, not raw private chat history.

Gateway run pattern:

```bash
CANARY_PROFILE=context-route-gateway-canary
hermes profile create "$CANARY_PROFILE" --clone repo-pm
hermes --profile "$CANARY_PROFILE" config set context_efficiency.enabled true
hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path logs/context_efficiency-gateway-canary.jsonl
hermes --profile "$CANARY_PROFILE" gateway status
# Start only the canary gateway instance according to the isolated canary deployment plan.
```

Gateway pass condition:

- At least 24 hours or 50 canary turns, whichever comes first.
- 0 delivery regressions: no missing final replies, duplicate replies, topic leakage, approval wedge, gateway crash, or platform routing change attributable to the canary.
- 0 telemetry-induced exceptions in gateway logs.
- Report mismatch rate remains within CLI-canary thresholds.
- Operators confirm no canary logs were written under the default/live profile.

## Gate 6: rollback criteria

Rollback means disable the canary config and preserve the log/report artifacts for analysis unless they contain sensitive data.

Immediate rollback triggers:

- any secret, token, credential, raw private transcript, or full memory-profile leak in telemetry;
- any observed behavior change caused by advisor telemetry;
- any gateway delivery/routing regression correlated with enabling telemetry;
- telemetry write errors that propagate into tool execution or user responses;
- report parser errors on valid log events;
- repeated-run instability that violates Gate 3 after labels are checked;
- operator confusion where canary and live profiles cannot be clearly distinguished.

Rollback commands:

```bash
CANARY_PROFILE=context-route-canary
hermes --profile "$CANARY_PROFILE" config set context_efficiency.enabled false
hermes --profile "$CANARY_PROFILE" config set context_efficiency.log_path logs/context_efficiency.disabled.jsonl
hermes --profile "$CANARY_PROFILE" gateway restart || true
python -m agent.context_efficiency_report "$HOME/.hermes/profiles/$CANARY_PROFILE/logs/context_efficiency.jsonl" --json \
  > /tmp/context-efficiency-rollback-report.json || true
```

For the default/live profile, rollback verification is simply:

```bash
hermes config | grep -A 12 context_efficiency
# Expected in live/default before promotion: enabled: false
```

## Promotion decision checklist

A promotion request must include:

- privacy/logging gate evidence;
- eval prompt-set path and label summary;
- per-run report JSON artifacts;
- mismatch triage table;
- gateway canary summary if gateway promotion is requested;
- explicit statement that telemetry remains observational only;
- rollback command and owner.

## Implementation cards informed by this spec

1. Add a committed 30-50 prompt context-route eval set with labels and negative controls.
2. Add an eval harness that runs the prompt set against an isolated profile and emits per-prompt outcome JSON.
3. Add a stability report that compares multiple `context_efficiency_report --json` outputs and flags flips/must-not-use violations.
4. Add privacy scanning for telemetry logs as a focused test/helper.
5. Add canary profile/gateway runbook automation that refuses to target the default/live profile.
