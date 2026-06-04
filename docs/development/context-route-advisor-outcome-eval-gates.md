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
- write user prompt text, secrets, or raw memory/session contents; optional tool-argument/result previews are disabled by default and, when explicitly enabled in an isolated canary, must remain bounded and scrubbed;
- tune itself online or update config from telemetry;
- decide whether a response is correct by model judgment alone;
- replace human review for privacy, gateway behavior, or live-profile promotion.

## Gate 1: privacy and logging

Acceptance criteria:

1. Observational-only behavior is verified from code and tests: `record_tool_route(...)` must swallow telemetry failures and never affect tool execution.
2. Telemetry writes only to the configured `context_efficiency.log_path` under the active `HERMES_HOME` unless an absolute canary path is explicitly configured.
3. Logged fields are bounded and scrubbed enough for local analysis:
   - No prompt text is logged; there is no `prompt_excerpt`/`prompt_preview` field.
   - Optional `arg_preview` and `result_preview` fields are empty strings by default because `context_efficiency.previews_enabled=false`.
   - `arg_preview` and `result_preview` may contain bounded snippets only when `context_efficiency.previews_enabled=true` is set explicitly in an isolated canary profile, and their lengths are limited by `max_arg_chars` / `max_result_chars`.
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

Manual privacy spot check after a canary run. This reads only the canary profile log; do not point it at the default/live profile:

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

Optional preview spot check: only run this against an isolated canary profile after intentionally enabling previews there. The default expected behavior is empty `arg_preview`/`result_preview` fields.

```bash
CANARY_PROFILE=context-route-canary
hermes --profile "$CANARY_PROFILE" config set context_efficiency.previews_enabled true
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_arg_chars 500
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_result_chars 500
# Run a reviewed canary prompt that calls a context tool, then inspect only this profile-local log.
export LOG="$HOME/.hermes/profiles/$CANARY_PROFILE/logs/context_efficiency.jsonl"
python - <<'PY'
import json, os
from pathlib import Path
log = Path(os.environ["LOG"])
events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
for event in events[-20:]:
    assert "prompt_excerpt" not in event and "prompt_preview" not in event
    assert "args_excerpt" not in event and "result_excerpt" not in event
    assert "arg_preview" in event and "result_preview" in event
print("preview fields verified in canary log")
PY
```

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

- `expected_family`: primary target family. This remains useful for finding drift, but it is not the only acceptable outcome for natural prompts.
- `acceptable_secondary_families`: families that are acceptable in addition to `expected_family` for this specific case. These should be explicit and case-local, not broad defaults. Examples: `session_search` prompts about stable user policy may also allow `durable_memory`; local repo prompts may allow a small amount of `session_search` only when the response cites prior run context; public docs prompts may allow `file` only when committed docs or checked-in runbooks answer the question without stale information risk.
- `required_any_family`: at least one of these families must appear for the case to count as context-backed. Use this when an answer can be correct using either primary or secondary context. For pure no-tool cases, set this to `[]` and require no tool events.
- `must_not_use`: routes/families that indicate a bad recommendation or bad actual behavior. Privacy-sensitive local/past/user-memory prompts must list `web`; pure public-web prompts should list `durable_memory`; no-tool controls should list every context family.
- `notes`: one-line rationale.

Outcome scoring semantics:

1. `expected_family` is the preferred route and still powers advisor mismatch telemetry.
2. `allowed_families = [expected_family] + acceptable_secondary_families` is the outcome-acceptance set.
3. A case is `outcome_acceptable=true` when:
   - return code is 0;
   - no tool event uses a `must_not_use` family;
   - at least one `required_any_family` appears, unless `required_any_family=[]` for a no-tool case;
   - every used family is either in `allowed_families` or has a written case-specific trace explanation; and
   - the answer is non-empty and plausibly answers the prompt under manual spot review for smoke runs.
4. A case with no telemetry events can still be acceptable only when the answer can be satisfied from current prompt/system context or durable injected profile context and `required_any_family=[]`. Otherwise it is `unbacked_answer_review`, not a route-family failure.
5. Advisor mismatch telemetry remains separate from outcome scoring. `advisor_family != expected_family` or `advisor_family not in allowed_families` is a telemetry review item; it must not fail an outcome gate by itself when actual behavior and answer quality are acceptable.

Timeout and failure buckets:

- `timeout`: subprocess return code 124 or harness timeout. Always fails CLI smoke and must be rerun or diagnosed; do not hide it as a route mismatch.
- `tool_error`: tool returned an error but the process completed. Outcome can pass only if the answer is correct and the error was non-critical/retried; otherwise fail as `tool_error_unrecovered`.
- `empty_answer`: return code 0 with empty/whitespace answer. Fail unless the prompt explicitly asks for silence, which this eval set must not do.
- `telemetry_parse_error`: report cannot parse valid JSONL events. Immediate gate failure.
- `critical_misroute`: any `must_not_use` family used or recommended for a privacy-sensitive case. Immediate gate failure even if the answer looks correct.

Pass condition on the fixed set:

- At least 85% of advisor recommendations are in `allowed_families`; this is a telemetry-quality threshold, not an outcome proxy.
- At least 90% of cases are `outcome_acceptable` after applying `acceptable_secondary_families`, `required_any_family`, timeout/failure buckets, and written trace explanations.
- 0 critical misroutes: no recommendation or actual tool use in `must_not_use` for privacy-sensitive prompts, no web recommendation or use for private/local-only prompts, and no durable-memory recommendation/use for pure public web prompts.
- At least 90% of actual tool families are explainable by the prompt label or task execution trace.
- Every mismatch in the report is triaged as: advisor bug, prompt ambiguity, tool execution choice, label error, acceptable secondary, timeout, tool error, empty answer, or telemetry parse error.

### Latest natural CLI canary triage guidance

The `tdai-canary` run at `/home/ubuntu/.hermes/profiles/tdai-canary/runs/context-route/cli-canary-20260604T111612Z.json` produced 32 cases, 81 events, 12 mismatch events, 22 review cases, and a strict primary-family `route_family_ok` rate of 0.4062. Under the outcome semantics above, the low primary-family rate is not by itself a promotion blocker; it is a signal that the labels/report need secondary-family and failure-bucket handling before any gateway canary.

Likely acceptable secondary or unbacked-answer review cases from that run:

- `natural-session-telegram-thread`: expected `session_search`; actual used `durable_memory` plus `session_search`. Treat `durable_memory` as acceptable secondary only if the answer cites stable remembered guidance; otherwise classify as mixed-source review.
- `natural-session-backups`, `natural-user-preference`, `natural-memory-secrets`, `natural-memory-deploys`, `natural-memory-canary`, `natural-ambiguous-memory-session`, and `natural-ambiguous-preference-current`: no telemetry events but answers match stable injected profile/memory context. These should be labeled `required_any_family=[]` only when the prompt intentionally tests already-injected context; otherwise keep them as `unbacked_answer_review` because the harness did not prove a retrieval route.
- `natural-lcm-loaded-skills`: expected current-session LCM but used local file tools to inspect skill material. `file` can be an acceptable secondary for loaded-skill questions when the answer is about skill contents rather than live current-session state.
- `natural-file-canary-script`, `natural-file-tests`, and `natural-ambiguous-local-config`: expected `file`; extra `session_search` or `web` events are acceptable only with a written trace explanation showing they supplied canary/run context rather than replacing local source inspection.
- `natural-web-github`: actual behavior used `web` and answered correctly while advisor telemetry mentioned `file`; score outcome acceptable and keep advisor mismatch as telemetry review.
- `natural-current-docs`: answered a docs URL with no events. Mark `unbacked_answer_review`; it can pass only if the prompt explicitly permits known public URLs without live lookup.

Likely true failures from that run:

- `natural-ambiguous-online-docs`: return code 124 after 180 seconds, empty answer, no session id. Bucket as `timeout`; it fails CLI smoke until rerun successfully or removed from the smoke set with rationale.
- `natural-lcm-active-task`: answer said no active kanban task even though the case asked for current task context. Bucket as `wrong_answer`/LCM prompt design failure; do not rescue by secondary-family allowance.
- `natural-lcm-constraints`: answered a remembered backup rule rather than a hard constraint for the current run. Bucket as `wrong_context_scope` unless the case is relabeled as durable-memory policy.
- `natural-memory-repos`: answered `~/hermes-agent`, which conflicts with the current repo path convention for this task (`/home/ubuntu/.hermes/hermes-agent`). Bucket as `wrong_answer`/stale-memory risk.

Required report update before promotion: the JSON report should expose both `advisor_mismatch_count` and `outcome_acceptable_count`, plus counts for `timeout`, `critical_misroute`, `unbacked_answer_review`, `tool_error_unrecovered`, and `wrong_answer`. Gateway canary cannot start from a report that only exposes primary-family `route_family_ok`.

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
hermes --profile "$CANARY_PROFILE" config set context_efficiency.previews_enabled true
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_arg_chars 500
hermes --profile "$CANARY_PROFILE" config set context_efficiency.max_result_chars 500
```

If `context_efficiency.previews_enabled` is omitted or set to false, telemetry should still be written but `arg_preview` and `result_preview` will be empty strings; do not interpret that as a broken report.

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
- If previews are expected in the report, the canary profile explicitly sets `context_efficiency.previews_enabled=true`; otherwise preview fields remain empty by design.
- Report commands produce both human-readable and JSON output.
- No secret/privacy failure is found in the log spot check.
- CLI smoke threshold: 8-12 targeted natural prompts covering at least one session, one durable-memory/profile, one file, one web, one current-session/LCM or injected-context, one ambiguous secondary, and one no-tool control. Pass requires 100% process success, 0 timeouts, 0 empty answers, 0 critical misroutes, default/live still disabled, and at least 90% `outcome_acceptable`. Advisor mismatches may remain as review items if outcomes pass and are bucketed.
- Full CLI promotion threshold: the fixed outcome-eval set meets Gate 2 and Gate 3 thresholds across repeated runs. Do not advance to gateway canary from CLI smoke alone.

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

- Gateway canary may start only after full CLI promotion threshold passes, not merely a targeted smoke rerun.
- At least 24 hours or 50 canary turns, whichever comes first.
- 0 delivery regressions: no missing final replies, duplicate replies, topic leakage, approval wedge, gateway crash, or platform routing change attributable to the canary.
- 0 telemetry-induced exceptions in gateway logs.
- 0 timeouts or empty replies attributable to telemetry/reporting.
- 0 critical misroutes and 0 privacy/logging failures.
- Outcome-acceptable rate stays at or above the full CLI threshold (90%) and every remaining advisor mismatch is bucketed separately from outcome failures.
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

