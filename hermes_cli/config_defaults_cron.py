"""Scheduler and delivery defaults; consumed by the shared config registry."""

CRON_DEFAULTS = {
    "catch_up_missed": True,  # False skips recurring misses beyond the local grace window.
    # Let cron-spawned agents use the cronjob toolset (the "cron-librarian" pattern). Off by
    # default: policy-denied in cron context to prevent unattended scheduling loops. Jobs
    # created this way are user-owned in the same flat jobs table. Interactive toolsets
    # (messaging/clarify) stay denied in cron regardless.
    "allow_agent_scheduling": False,
    # Pre-dispatch validation: before building any agent machinery, verify the provider API key
    # resolves (unless a fallback chain exists), attached skills are ready, and delivery
    # platforms are configured. Failure -> last_status=blocked_config, ONE alert, no LLM call.
    # False = fail during the run instead.
    "preflight": True,
    # Default model for cron jobs (WHAT model runs). Fire-time resolution: per-job pin >
    # cron.model > model.default (the main agent model). An unpinned job follows the main
    # model on every run; cron.model decouples the whole fleet from chat. "" = fall through.
    "model": "",
    # Inference provider paired with cron.model (NOT the scheduler provider below). "" = resolve
    # from global config.
    "model_provider": "",
    # Cron SCHEDULER provider (WHEN a due job fires). "" = built-in in-process 60s ticker. Name
    # an installed provider (plugins/cron_providers/<name>/ or $HERMES_HOME/plugins/ <name>/),
    # e.g. "chronos" (NAS-mediated managed cron for scale-to-zero). An unknown or unavailable
    # provider falls back to the built-in so cron never loses its trigger.
    "provider": "",
    # Chronos settings; consulted only when provider == "chronos". All non-secret — the agent
    # holds NO scheduler credentials (provision reuses the Nous Portal token).
    "chronos": {
        # NAS/portal base URL that arms/cancels one-shots and mints the inbound fire JWT (used
        # as the expected issuer).
        "portal_url": "https://portal.nousresearch.com",
        # This agent's publicly reachable base URL; NAS POSTs {callback_url}/api/cron/fire. ""
        # -> Chronos unavailable, resolver falls back to the built-in ticker.
        "callback_url": "",
        "expected_audience": "",  # Expected JWT audience (e.g. "agent:{instance_id}").
        # NAS JWKS URL for verifying the fire JWT signature. "" -> the fire endpoint refuses all
        # tokens (never an unsigned decode).
        "nas_jwks_url": "",
    },
    # Wrap delivered cron responses with a task-name header and "The agent cannot see this
    # message" footer. False = clean output.
    "wrap_response": True,
    # Create a fresh private Telegram DM topic for each cron output.
    "telegram_new_thread_per_output": False,
    "delivery": {  # Delivery behaviour for cron output sent through a live gateway adapter.
        # Mark cron deliveries FINAL so the platform pushes them (Telegram's "important" mode
        # otherwise sends with disable_notification=True and briefs look undelivered). False =
        # silent, no-push deliveries.
        "notify": True,
    },
    # Make cron deliveries CONTINUABLE (user can reply to a brief with it in context). False
    # keeps deliveries isolated to the job's session; per-job `attach_to_session` overrides.
    # Thread-capable platforms (Telegram topics, Discord/Slack threads) get a seeded thread per
    # job via create_handoff_thread; DM-only platforms mirror the brief into the target DM
    # session. Appended at a turn boundary via mirror_to_session, cached system prompt
    # untouched. User-written bare platforms address home conversations, unlike `all`
    # broadcast expansions, which do not gain mirror eligibility.
    "mirror_delivery": False,
    # Max due jobs run in parallel per tick. None/0 = unbounded (thread count only); 1 = serial.
    # Env override: HERMES_CRON_MAX_PARALLEL.
    "max_parallel_jobs": None,
    # save_job_output keeps the N most recent .md files per job; 0 or negative disables pruning
    # (for externally managed cleanup).
    "output_retention": 50,
    # Timeout (seconds) for a no-agent cron script. Env: HERMES_CRON_SCRIPT_TIMEOUT. Keep in
    # sync with cron.scheduler._DEFAULT_SCRIPT_TIMEOUT.
    "script_timeout_seconds": 3600,
    # Timeout (seconds) for SessionDB() init inside cron jobs: state.db open/migrate has no
    # timeout of its own against a wedged sqlite3.connect, and an unbounded hang wedges the
    # job's dispatch guard forever. Env: HERMES_CRON_SESSION_DB_TIMEOUT. 0 = unlimited.
    "session_db_timeout_seconds": 10,
    # Timeout (seconds) per media attachment send during gateway delivery; large attachments
    # (long TTS audio, big exports) need more than 30s. Env: HERMES_CRON_MEDIA_SEND_TIMEOUT.
    # Keep in sync with cron.scheduler._DEFAULT_MEDIA_SEND_TIMEOUT.
    "media_send_timeout_seconds": 300,
    # Managed systemd gateway with no user session (containers, no linger): false runs
    # cron jobs as a direct external subprocess (warns once; no cgroup isolation), true
    # fails closed with the enable-linger remedy. Kanban always requires a scope.
    "require_restart_safe_scope": False,
    # A job failing with the SAME error alerts once, then stays silent for this many hours
    # before one reminder ping (the run is still recorded; `hermes cron incidents` shows it).
    # A green run or a different error alerts again immediately; `hermes cron incidents ack`
    # silences a signature for good. 0 = re-alert on every failing run. Keep in sync with
    # cron.scheduler.DEFAULT_FAILURE_REPEAT_ALERT_HOURS.
    "failure_repeat_alert_hours": 6,
}
