"""SessionStore storage plumbing: per-profile SessionDB handle resolution and the routing-index
load/save paths (state.db gateway_routing primary, sessions.json legacy mirror). Mixin split out of
``gateway/session.py``; bound onto ``SessionStore`` via the MRO."""

from __future__ import annotations

import contextlib
import logging
import json
import threading
import sys
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional
from utils import atomic_json_write

if TYPE_CHECKING:
    from gateway.session import SessionEntry, SessionSource

# Log-record parity with the origin module.
logger = logging.getLogger("gateway.session")

# "No SessionDB pinned" sentinel: lets ``_db`` distinguish "resolve from the active scope" from a
# deliberate ``store._db = None`` (JSONL fallback).
_DB_UNPINNED = object()

# Self-documenting sentinel written first into sessions.json; "_" keys are skipped on load.
_SESSIONS_JSON_README = (
    "LEGACY MIRROR of the gateway routing index (the primary copy lives in the gateway_routing "
    "table in ~/.hermes/state.db). Maps messaging session keys (agent:main:<platform>:...) to "
    "active session IDs. This is NOT the session list. ALL sessions (CLI, TUI, and gateway) live "
    "in ~/.hermes/state.db and are shown by `hermes sessions list` and `/sessions`. Disable this "
    "file with `gateway.write_sessions_json: false` in config.yaml."
)


def _is_live_system_guard(exc: BaseException) -> bool:
    """Test-isolation guard: must stay a loud failure and is never cached."""
    return isinstance(exc, RuntimeError) and "live-system guard" in str(exc)


class SessionPersistenceMixin:
    """SessionStore storage plumbing: SessionDB handle resolution and routing-index load/save."""

    def retire_runtime_sessions(self, session_ids):
        """Publish committed retirement; durable fences reject delayed saves."""
        with self._lock:
            targets = set(session_ids)
            for key, entry in list(self._entries.items()):
                if entry.session_id in targets:
                    del self._entries[key]
            for sid in targets:
                self._session_owner_hints.pop(sid, None)

    def fence_retired_routes(self, session_ids, *, namespace, expiring=()) -> None:
        """Durably retire *session_ids* in the routing index's own database (fences + route
        drop in one transaction). A delete committed in another profile's store fences only that
        store; the shared index lives in the launch home, so its saves need the fence here too,
        keyed by the deleting profile's routing *namespace* (``retire_gateway_routes``).
        Raises when the routing store cannot take the fence: the delete must not report success."""
        retire = self._routing_db_method("retire_gateway_routes")
        if retire is None:
            raise RuntimeError("routing store unavailable for deletion fence")
        retire(session_ids, namespace=namespace, expiring=expiring)

    def strip_retired_mirror_routes(self, session_ids=None) -> None:
        """Drop from an existing sessions.json every route a routing save now skips as retired
        (``route_retired``, the check ``replace_gateway_routing_entries`` applies), only those naming
        *session_ids* when given. Runs whatever ``write_sessions_json`` says and never creates the
        file: the mirror is derived, and a retired route left in it would come back through the
        next start's legacy import once a prune's fence ages out."""
        db = self._routing_db
        sessions_file = self.sessions_dir / "sessions.json"
        if db is None or not sessions_file.exists():
            return
        from hermes_state_mutation_retirement import route_retired
        targets = None if session_ids is None else set(session_ids)
        with self._lazy("_save_lock", threading.Lock):  # never interleaves with a full save's write
            with open(sessions_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            kept = {}
            for key, entry in data.items():
                sid = entry.get("session_id") if isinstance(entry, dict) else None
                if (key.startswith("_") or not isinstance(sid, str) or (targets is not None and sid not in targets)
                        or not route_retired(db, key, json.dumps({"session_id": sid}))):
                    kept[key] = entry
            if len(kept) < len(data):
                atomic_json_write(sessions_file, kept, mode=0o600)

    def _open_session_db_for_active_scope(self, db_path: Optional[Path] = None):
        """SessionDB for the active profile scope. ``db_path`` pins the store; otherwise
        ``_default_db_path()`` follows the context-local HERMES_HOME (resolved per call so
        multiplexed profiles reach their own store). Handles are cached per path; failed opens enter
        a bounded backoff during which callers keep using the JSONL fallback.

        Resolving here rather than once in ``__init__`` is the whole fix for #88532: it lets the scoping
        that the multiplexed inbound path already performs actually reach session storage.
        """
        from hermes_state import _default_db_path
        from hermes_state_registry import acquire

        path = Path(db_path) if db_path is not None else Path(_default_db_path())

        def _open():
            try:
                return acquire(path)  # process-wide registry: one writer per path
            except Exception as e:
                if not _is_live_system_guard(e):
                    print(f"[gateway] Warning: SQLite session store unavailable, falling back to JSONL: {e}")
                raise

        return self._db_handle_cache.get(path, _open, non_cacheable=_is_live_system_guard)

    def _pinned_db(self):
        """Return the explicitly pinned DB (``store._db = x``), else ``_DB_UNPINNED``."""
        return getattr(self, "_db_pinned", _DB_UNPINNED)

    @property
    def _db(self):
        """The SessionDB for the active profile scope, or a pinned override. Assigning ``store._db``
        pins that value for every subsequent read (tests install a fake or disable the DB with
        ``store._db = None``); unpinned, each read resolves the scope so a multiplexed profile's
        writes reach its own store."""
        pinned = self._pinned_db()
        return self._open_session_db_for_active_scope() if pinned is _DB_UNPINNED else pinned

    @_db.setter
    def _db(self, value) -> None:
        self._db_pinned = value

    @property
    def _routing_db(self):
        """The one store that owns the routing index, whatever scope is active. ``_entries`` is one
        flat dict holding every profile's keys, so it must persist to ONE file (``_routing_home``),
        not whichever profile is scoped — otherwise a mid-turn rewrite and the unscoped startup
        load see different copies and crash markers under a secondary profile go unrecovered. A
        pinned handle still wins; bare test instances lacking the handle cache report no DB.

        Reading it through ``_db`` made that file whichever profile happened to be scoped at the time: a
        whole-index rewrite during one profile's turn copied every other profile's routing rows into that
        profile's store, and startup — which runs unscoped — then loaded a different copy than the one the
        last writer produced. See #66887.
        """
        pinned = self._pinned_db()
        if pinned is not _DB_UNPINNED:
            return pinned
        home = getattr(self, "_routing_home", None)
        try:
            if home is None:
                return self._db
            return self._open_session_db_for_active_scope(db_path=home / "state.db")
        except Exception:
            return None

    def _named_profile_for_key(self, session_key: Optional[str]) -> Optional[str]:
        """The non-default profile that owns *session_key*, or None (ambient store is authoritative:
        multiplexing off, or legacy ``agent:main``). Deliberately does NOT cover "that profile has
        no directory" — ownership and resolvability are separate questions for ``_db_for_key``."""
        if not getattr(self.config, "multiplex_profiles", False):
            return None
        profile = self._profile_from_session_key(session_key)
        return None if not profile or profile == "default" else profile

    def _profile_home_for_key(self, session_key: Optional[str]) -> Optional[Path]:
        """HERMES_HOME of the profile owning *session_key*, or None (no named owner or
        unresolvable)."""
        profile = self._named_profile_for_key(session_key)
        if profile is None:
            return None
        cache = self._profile_home_cache
        if profile in cache:
            return cache[profile]
        home: Optional[Path] = None
        try:
            from hermes_cli.profiles import get_profile_dir, profile_exists
            if profile_exists(profile):
                home = Path(get_profile_dir(profile))
        except Exception as exc:
            logger.debug("Could not resolve profile home for %r: %s", session_key, exc)
            home = None
        # Only hits are memoized: a profile directory can be provisioned *after* startup (enrollment
        # bridge), and a cached miss would pin that profile's rows to the ambient store for life.
        if home is not None:
            cache[profile] = home
        return home

    def _db_for_key(self, session_key: Optional[str]):
        """The SessionDB holding *session_key*'s rows, whatever scope is active (the owning profile
        is encoded in the key). ``_db`` follows the ambient HERMES_HOME that only the inbound message
        path installs; unscoped background work (expiry watcher) would otherwise write profile rows
        into the ROOT store until the stale-route self-heal drops a live conversation.

        Background work runs unscoped while operating on every profile's keys out of the single process-wide
        ``_entries`` dict — ``_session_housekeeping_watcher`` is the clearest case — so it reads and writes the
        ROOT store for rows that actually live under ``profiles/<name>/state.db``. The two writers then
        drift apart on the same logical session until the routing index disagrees with the row and the
        #54878 self-heal drops a live conversation (#66887).
        """
        pinned = self._pinned_db()
        if pinned is not _DB_UNPINNED:
            return pinned
        profile = self._named_profile_for_key(session_key)
        if profile is None:
            # Default-profile (``agent:main``) rows belong to the launch home, not to whichever
            # profile's scope happens to be active: a scoped drain tick or cron mirror touching a
            # default chat used to write its rows into the secondary's store (#102157's picture).
            routing_home = getattr(self, "_routing_home", None)
            if routing_home is None or not getattr(self.config, "multiplex_profiles", False):
                return self._db
            try:
                return self._open_session_db_for_active_scope(db_path=routing_home / "state.db")
            except Exception:
                return None
        home = self._profile_home_for_key(session_key)
        if home is None:
            # Falling back to the ambient store would split ONE session identity across two
            # physical stores — fail closed; callers already handle a missing DB.
            logger.warning(
                "gateway.session: profile %r has no resolvable home (key %r); refusing to fall "
                "back to the ambient store", profile, session_key)
            return None
        try:
            return self._open_session_db_for_active_scope(db_path=home / "state.db")
        except Exception:
            return None  # same contract as ``_db``: a failed open degrades to JSONL fallback

    def _owner_key_for_session_id(self, session_id: Optional[str]) -> Optional[str]:
        """The routing key that owns *session_id*, or None. The published index is authoritative;
        ``_session_owner_hints`` covers the window where ownership is proven but routing not yet
        published. Deliberately lock-free: several callers already hold ``_lock``."""
        if not session_id:
            return None
        try:
            for entry in list(self._entries.values()):
                if entry.session_id == session_id:
                    return entry.session_key
        except Exception:
            pass  # bare stores / foreign entry objects in suites
        return (getattr(self, "_session_owner_hints", None) or {}).get(session_id)

    def _db_for_session_id(self, session_id: Optional[str]):
        """The SessionDB holding *session_id*'s row (owner from the index or a pre-published hint;
        unknown ids fall back to the ambient store)."""
        if not session_id:
            return self._db
        return self._db_for_key(self._owner_key_for_session_id(session_id))

    def close_all_db_handles(self) -> None:
        """Close every SessionDB handle this store opened (one per path). Closing only ``store._db``
        would strand secondary profiles' handles with their WAL lock held ('database is locked' on
        restart). Drained under the lock, closed outside it; a pinned handle is the pinner's."""
        def _close(db) -> None:
            from hermes_state_registry import release_or_close  # shared instances no-op on close()
            try:
                release_or_close(db)
            except Exception as exc:
                logger.debug("SessionDB close error during handle sweep: %s", exc)

        self._db_handle_cache.close_all(_close)

    def _ensure_loaded(self) -> None:
        """Load sessions index from disk if not already loaded."""
        with self._lock:
            self._ensure_loaded_locked()

    def _entry_locked(self, session_key: str) -> Optional[SessionEntry]:
        """Load the index and return the entry for *session_key*. Lock held."""
        self._ensure_loaded_locked()
        return self._entries.get(session_key)

    def _routing_scope(self) -> str:
        """Namespace for this store's gateway_routing rows: the resolved sessions_dir, so stores
        with different dirs never share entries."""
        try:
            return str(Path(self.sessions_dir).resolve())
        except Exception:
            return str(self.sessions_dir)

    def _routing_db_method(self, name: str):
        """Bound ``_routing_db.<name>`` if the handle exists and has it, else None."""
        method = getattr(self._routing_db or None, name, None)
        return method if callable(method) else None

    @staticmethod
    def _chat_labels_meta_key(platform, scope_id, chat_id) -> str:
        return f"gateway_chat_labels:{getattr(platform, 'value', platform)}:{scope_id or ''}:{chat_id}"

    def record_chat_labels(self, source: SessionSource) -> bool:
        """Persist *source*'s ``chat_name`` / ``chat_topic`` as the chat's current labels, at the
        moment they are observed; True once written. A session origin cannot answer "what is this
        chat called now": it is written when its session is created, which can be after a newer
        observation (a delayed first turn), and a reset inherits it under a fresh ``created_at``.

        False when there is no database handle right now (open failed, backoff, JSONL fallback):
        nothing was written, so the caller must not treat these labels as recorded."""
        setter = self._routing_db_method("set_meta")
        if setter is None:
            return False
        setter(self._chat_labels_meta_key(source.platform, source.scope_id, source.chat_id),
               json.dumps([source.chat_name, source.chat_topic]))
        return True

    def chat_labels(self, platform, scope_id, chat_id) -> Optional[tuple]:
        """The ``(chat_name, chat_topic)`` last recorded for a chat, or None when none was. Raises
        when the store cannot be read, including when there is no database handle right now:
        "unreadable now" is not "never labelled"."""
        getter = self._routing_db_method("get_meta")
        if getter is None:
            raise LookupError("session database unavailable")
        raw = getter(self._chat_labels_meta_key(platform, scope_id, chat_id))
        if not raw:
            return None
        chat_name, chat_topic = json.loads(raw)
        return chat_name, chat_topic

    def _load_routing_rows_locked(self) -> bool:
        """Load state.db routing entries into ``_entries``; False when there is no loader or the
        load failed (warned). Lock held."""
        loader = self._routing_db_method("load_gateway_routing_entries")
        if loader is None:
            return False
        try:
            for key, entry_json in loader(scope=self._routing_scope()).items():
                entry = self._routing_entry_from_json(key, entry_json)
                if entry is not None:
                    self._entries[key] = entry
            return True
        except Exception as e:
            logger.warning("gateway.session: state.db routing load failed: %s", e)
            return False

    @staticmethod
    def _routing_entry_from_json(key: str, entry_json: str) -> Optional[SessionEntry]:
        """Parse one gateway_routing row; None (with a warning) when invalid."""
        from gateway.session import SessionEntry
        try:
            entry_data = json.loads(entry_json)
            if isinstance(entry_data, dict):
                return SessionEntry.from_dict(entry_data)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Skipping invalid routing entry %r: %s", key, e)
        return None

    def _ensure_loaded_locked(self) -> None:
        """Load the routing index (lock held). state.db ``gateway_routing`` is primary;
        sessions.json is the legacy import for keys the DB lacks (persisted on the next _save).

        Read order (#9006 follow-up): the ``gateway_routing`` table in state.db is the primary source;
        sessions.json is the legacy import path for pre-migration installs (its entries are folded in for
        keys the DB doesn't have, then persisted to the DB on the next _save).
        """
        if self._loaded:
            self._reconcile_recovered_routing_locked()
            return
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        db_load_succeeded = self._load_routing_rows_locked()
        db_had_entries = db_load_succeeded and bool(self._entries)
        mirror_retired = self._import_legacy_sessions_json(db_had_entries)
        self._loaded = True
        self._routing_db_loaded = db_load_succeeded
        self._routing_fallback_baseline = None if db_load_succeeded else self._entries_as_dicts()
        if mirror_retired and db_load_succeeded:
            # Rewrite the mirror while the fences that kept those routes out still hold: a prune's
            # ages out, and the import would then restore the retired route.
            self._save()
            if not getattr(self, "_write_sessions_json", True):
                # That save left an existing mirror as it was: strip the routes from it directly,
                # or every later start imports them again.
                try:
                    self.strip_retired_mirror_routes()
                except Exception:
                    logger.warning("gateway.session: could not strip retired routes from sessions.json", exc_info=True)
        # A hard crash skips graceful shutdown and leaves sessions.json pointing at ended sessions.
        self._prune_stale_sessions_locked()

    def _import_legacy_sessions_json(self, db_had_entries: bool) -> int:
        """Legacy import: sessions.json fills only keys the DB lacks. Lock held. Returns how many
        mirror entries it skipped as retired routes."""
        from gateway.session import SessionEntry
        sessions_file = self.sessions_dir / "sessions.json"
        retired = 0
        if not sessions_file.exists():
            return retired
        try:
            with open(sessions_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            imported = 0
            for key, entry_data in data.items():
                # "_"-prefixed keys are sentinels (e.g. "_README"), not entries.
                if key.startswith("_") or key in self._entries:
                    continue
                if not isinstance(entry_data, dict):  # corrupt file must not abort the whole load
                    logger.warning(
                        "Skipping invalid session entry %r: expected dict, got %s", key,
                        type(entry_data).__name__)
                    continue
                try:
                    from hermes_state_mutation_retirement import route_retired
                    db = self._routing_db
                    if db is not None and route_retired(db, key, json.dumps(
                            {'session_id': entry_data['session_id']})):
                        retired += 1
                        continue
                    self._entries[key] = SessionEntry.from_dict(entry_data)
                    imported += 1
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning("Skipping invalid session entry %r: %s", key, e)
            if imported and db_had_entries:
                logger.info(
                    "gateway.session: imported %d legacy sessions.json entr%s missing from "
                    "state.db routing table", imported, "y" if imported == 1 else "ies")
        except Exception as e:
            print(f"[gateway] Warning: Failed to load sessions: {e}")
        return retired

    def _prune_stale_sessions_locked(self) -> None:
        """Remove routing entries whose session has ended in state.db (startup, lock held). Stale ==
        ``end_reason IS NOT NULL``; rows absent from the DB are kept; a ``None`` DB handle is a
        no-op; DB errors are non-fatal."""
        if not self._entries:
            return
        stale_keys: list = []
        recovered_keys = 0
        try:
            for key, entry in self._entries.items():
                # Ask the store that owns the key, not the ambient handle, or a live
                # secondary-profile session gets pruned on the root copy.
                db = self._db_for_key(key)
                if db is None:
                    continue
                row = db.get_session(entry.session_id)
                if row is None or row.get("end_reason") is None:
                    continue
                verdict = self._stale_entry_verdict(key, entry, row)
                if verdict == "prune":
                    stale_keys.append(key)
                elif verdict is not None:
                    self._entries[key] = verdict
                    recovered_keys += 1
        except Exception as exc:
            logger.warning("gateway.session: stale-entry pruning skipped due to DB error: %s", exc)
            return
        for key in stale_keys:
            del self._entries[key]
        if stale_keys or recovered_keys:
            self._save()

    def _stale_entry_verdict(self, key: str, entry, row):
        """For a routing entry whose row has ended: ``"prune"``, a replacement entry (repoint), or
        None (keep as-is)."""
        from gateway.session_lifecycle import _now
        local_target = self._local_receipt_target(entry)
        if local_target is not None:
            # The authority's receipt names the route's target (a compression child after a
            # split); its first admitted turn reopens a stamped row. A peer-row recovery here
            # adopted an empty, never-admitted replacement instead (#140642).
            if local_target == entry.session_id:
                return None
            import copy
            repointed = copy.copy(entry)
            repointed.session_id = local_target
            logger.warning(
                "gateway.session: repointing stale sessions.json entry %r from ended %s "
                "(end_reason=%r) to its local receipt target %s", key, entry.session_id,
                row["end_reason"], local_target)
            return repointed
        recovered_entry = None
        if entry.origin is not None:
            try:
                recovered_entry = self._recover_session_from_db(
                    session_key=key, source=entry.origin, now=_now(), raise_on_lookup_error=True)
            except Exception as exc:
                # Indeterminate: keep the only routing handle.
                logger.debug(
                    "gateway.session: recovery lookup failed for stale sessions.json entry %r -> "
                    "%s: %s", key, entry.session_id, exc)
                return None
        # Compression-ended parent with a newer live child for the same peer: repoint instead of
        # dropping, or queued/resume-pending work vanishes until the next message.
        if recovered_entry is not None and recovered_entry.session_id != entry.session_id:
            logger.warning(
                "gateway.session: repointing stale sessions.json entry %r from ended %s "
                "(end_reason=%r) to recovered %s", key, entry.session_id, row["end_reason"],
                recovered_entry.session_id)
            if entry.prompt_pin and (
                self._compression_tip_for_session_id(entry.session_id) == recovered_entry.session_id
            ):
                # The compression child continues this conversation: its internal turns keep the
                # pinned system bytes (the rebuilt entry is minimal and would drop them).
                recovered_entry.prompt_pin = entry.prompt_pin
            return recovered_entry
        # Same-id recovery == successful resume: keep the ORIGINAL entry object (the recovered one
        # is rebuilt minimal and would drop counters, model_override, resume markers, metadata).
        # A non-None recovery with the SAME session id is a successful resume (all recovery gates passed,
        # row reopened): keep the routing entry — it is proven valid, not a dead route (#95957). Nothing in
        # sessions.json changes, so no save is needed for this branch.
        if recovered_entry is not None:
            logger.info(
                "gateway.session: reopened ended session %s for sessions.json entry %r "
                "(end_reason=%r); keeping route", entry.session_id, key, row["end_reason"])
            return None
        logger.warning(
            "gateway.session: pruning stale sessions.json entry %r -> %s (end_reason=%r); left by "
            "a crashed gateway", key, entry.session_id, row["end_reason"])
        return "prune"

    def _entries_as_dicts(self) -> dict[str, Any]:
        """Serializable snapshot of ``_entries``. Lock held."""
        return {key: entry.to_dict() for key, entry in self._entries.items()}

    def _save(self) -> None:
        """Persist the routing index while the caller holds ``_lock``.

        The content-free caller name is forwarded to SessionDB latency
        telemetry so any remaining expensive full rewrite is attributable.
        """
        data, generation = self._snapshot_routing_locked()
        self._persist_routing_data(
            data,
            generation,
            reason=sys._getframe(1).f_code.co_name,
        )



    def _next_routing_generation_locked(self) -> int:
        """Bump and return the shared routing counter (lock held). Full snapshots AND single-entry
        fast saves MUST allocate from this one counter: the stale-write protection is a total order
        over serialization times and silently breaks otherwise."""
        self._routing_generation = getattr(self, "_routing_generation", 0) + 1
        return self._routing_generation

    def _reconcile_recovered_routing_locked(self) -> None:
        """Merge authoritative rows after a fallback-only startup load."""
        baseline = getattr(self, "_routing_fallback_baseline", None)
        if getattr(self, "_routing_db_loaded", False) or baseline is None:
            return
        loader = self._routing_db_method("load_gateway_routing_entries")
        if loader is None:
            return
        try:
            durable = loader(scope=self._routing_scope())
        except Exception as exc:
            logger.warning("gateway.session: recovered state.db routing load failed: %s", exc)
            return
        current = self._entries_as_dicts()
        for key, entry_json in durable.items():
            durable_entry = self._routing_entry_from_json(key, entry_json)
            if durable_entry is None:
                continue
            if key not in baseline:
                # A key created while on fallback wins over a DB-only key; otherwise restore the
                # authoritative row that fallback never saw.
                self._entries.setdefault(key, durable_entry)
            elif key not in current:
                continue  # loaded from fallback and deliberately removed
            elif current[key] == baseline[key]:
                self._entries[key] = durable_entry  # unchanged fallback data yields to the DB copy
        self._routing_db_loaded = True
        self._routing_fallback_baseline = None

    def _snapshot_routing_locked(self) -> tuple[dict[str, Any], int]:
        """Capture immutable routing data and a monotonic pending generation."""
        self._reconcile_recovered_routing_locked()
        data = {key: entry.to_dict() for key, entry in self._entries.items()}
        generation = self._next_routing_generation_locked()
        self._register_pending_routing_generation(generation)
        return data, generation



    def _try_exact_routing_delete(self, db, deleted_keys, data, generation, reason) -> bool:
        exact_delete = getattr(db, "delete_gateway_routing_entries", None)
        if not deleted_keys or not callable(exact_delete):
            return False
        if self._has_older_pending_routing_generation(generation):
            return False
        effective_keys = sorted(key for key in deleted_keys if key not in data)
        try:
            if effective_keys:
                exact_delete(effective_keys, scope=self._routing_scope(), reason=reason)
            return True
        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
            logger.warning("gateway.session: exact routing delete failed (%s); falling back to full reconciliation", exc)
            return False

    def _fold_newer_fast_routing(self, data, generation, point_key):
        """Merge newer point publications without advancing a full snapshot's watermark."""
        fast_persisted = getattr(self, "_fast_persisted_entries", None)
        point_already_saved = False
        if point_key is not None and fast_persisted:
            persisted = fast_persisted.get(point_key)
            point_already_saved = persisted is not None and persisted[0] >= generation
        if fast_persisted:
            for key, (revision, entry_json) in fast_persisted.items():
                if revision > generation:
                    data[key] = json.loads(entry_json)
        return point_already_saved, fast_persisted

    def _persist_routing_data(
        self,
        data: dict[str, Any],
        generation: int,
        *,
        reason: str = "unspecified",
        point_key: Optional[str] = None,
        deleted_keys: Optional[set[str]] = None,
        require_state_db: bool = False,
    ) -> bool:
        """Serialize routing persistence through one durable write lock.

        ``point_key`` requests one state.db UPSERT while the complete snapshot
        still refreshes the restart-critical legacy mirror. A point write is
        tracked per key: it cannot advance the complete-snapshot watermark,
        because an older delayed reconciliation may carry an unrelated key
        deletion. If the UPSERT is unavailable or fails, fall back to the
        ordinary atomic full reconciliation. Return ``False`` only when a
        structural point rebind cannot reach either state.db write path; its
        caller then rolls the in-memory compare-and-swap back.
        ``deleted_keys`` requests an exact structural delete, but only when
        every older captured generation has already resolved; otherwise
        ordering safety requires the ordinary atomic full reconciliation.
        """
        self._register_pending_routing_generation(generation)
        save_lock = getattr(self, "_save_lock", None)
        if save_lock is None:
            save_lock = threading.Lock()
            self._save_lock = save_lock
        try:
            with save_lock:
                if generation <= getattr(self, "_persisted_routing_generation", 0):
                    return True
                # Fold in single-entry upserts with a newer revision than this
                # snapshot (see _save_entry): revisions share the routing
                # generation counter, so a fast record numbered above us was
                # serialized after us and a delayed full rewrite must not
                # regress it.
                point_already_saved, fast_persisted = self._fold_newer_fast_routing(data, generation, point_key)
                point_saved = False
                full_reconciled = False
                point_entry_json: Optional[str] = None
                point_failed = False
                _db = getattr(self, "_routing_db", None)
                db_saved = bool(_db and point_already_saved)
                if _db:
                    if self._try_exact_routing_delete(_db, deleted_keys, data, generation, reason):
                        db_saved = full_reconciled = True

                    point_saver = getattr(_db, "save_gateway_routing_entry", None)
                    if (
                        not db_saved
                        and point_key is not None
                        and point_key in data
                        and callable(point_saver)
                    ):
                        try:
                            point_entry_json = json.dumps(data[point_key])
                            point_saver(
                                point_key,
                                point_entry_json,
                                scope=self._routing_scope(),
                            )
                            db_saved = True
                            point_saved = True
                        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                            point_failed = True
                            logger.warning(
                                "gateway.session: structural point routing save failed "
                                "for %r (%s); falling back to full reconciliation",
                                point_key,
                                exc,
                            )

                    replacer = getattr(_db, "replace_gateway_routing_entries", None)
                    if not db_saved and callable(replacer):
                        try:
                            fallback = point_failed or bool(deleted_keys)
                            retired = replacer(
                                {k: json.dumps(v) for k, v in data.items()},
                                scope=self._routing_scope(),
                                reason=(f"{reason}_fallback" if fallback else reason)[:64],
                            )
                            if retired:
                                data = {k: v for k, v in data.items() if k not in retired}
                            db_saved = True
                            full_reconciled = True
                        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                            logger.warning(
                                "gateway.session: state.db routing save failed: %s", exc
                            )

                if require_state_db and not db_saved:
                    return False
                if point_key is not None and _db and not db_saved:
                    return False

                if getattr(self, "_write_sessions_json", True) or not db_saved:
                    try:
                        self._save_sessions_json(data)
                    except BaseException as exc:
                        if not db_saved:
                            raise
                        # state.db is authoritative. A failed best-effort legacy
                        # mirror must not roll back a transition already committed
                        # there or leave the caller believing persistence failed.
                        logger.warning(
                            "gateway.session: legacy sessions.json mirror save failed: %s",
                            exc,
                        )

                # Only a complete durable reconciliation (including a safe exact
                # delete with no older pending generation) can supersede every
                # older revision. Point UPSERTs remain per-key ordered.
                persisted_complete = (
                    _db is None
                    or full_reconciled
                    or (point_key is None and db_saved)
                )
                if persisted_complete:
                    self._persisted_routing_generation = generation
                    if fast_persisted:
                        for key in [
                            k for k, (rev, _) in fast_persisted.items()
                            if rev <= generation
                        ]:
                            del fast_persisted[key]
                elif point_saved:
                    if fast_persisted is None:
                        fast_persisted = {}
                        self._fast_persisted_entries = fast_persisted
                    fast_persisted[point_key] = (generation, point_entry_json)
                return True
        finally:
            self._unregister_pending_routing_generation(generation)



    def _save_sessions_json(self, data: dict[str, Any]) -> None:
        """Write the legacy sessions.json mirror of the routing index (atomic + fsync)."""
        atomic_json_write(self.sessions_dir / "sessions.json", {"_README": _SESSIONS_JSON_README, **data}, mode=0o600)

    def _save_entries(
        self,
        *,
        reason: Optional[str] = None,
        point_key: Optional[str] = None,
    ) -> None:
        """Persist structural routing-index changes from a stable snapshot."""
        with self._lock:
            data, generation = self._snapshot_routing_locked()
        self._persist_routing_data(
            data,
            generation,
            reason=reason or sys._getframe(1).f_code.co_name,
            point_key=point_key,
        )



    def _save_entry(
        self,
        session_key: str,
        *,
        entry_data: Optional[dict[str, Any]] = None,
        lock_held: bool = False,
    ) -> None:
        """Persist ONE routing entry via UPSERT — the per-turn fast path.

        Steady-state turns and other one-key metadata/flag mutations only
        change one entry. Routing them through the full index rewrite
        re-serializes every entry, DELETE+INSERTs every gateway_routing row,
        and dumps+fsyncs a multi-MB sessions.json. A single-row UPSERT keeps
        the durable state.db mapping current without that global rewrite.

        Correctness constraints this path relies on:

        - The key -> session_id mapping never changes here. Structural
          one-key transitions (create/recover/reset/switch and compression-tip
          heals) use the generation-ordered point path, which also refreshes
          the legacy sessions.json mirror. Multi-key structural changes still
          use full reconciliation. Between structural saves the mirror may
          lag in metadata only; every remaining sessions.json reader is
          a legacy fallback and state.db stays primary, so restart
          rebinding is unaffected.

        - Ordering vs concurrent writers: the entry is serialized under
          ``_lock`` together with a revision allocated from the routing
          generation counter, so every snapshot — fast or full — carries
          a unique, monotonically increasing number, and a higher number
          always means same-or-newer data for this key.  Under
          ``_save_lock`` the upsert is skipped when a snapshot numbered
          above ours already persisted this key: a FULL snapshot
          (``_persisted_routing_generation``) or another fast save of
          the same key (``_fast_persisted_entries``).  Either contains a
          same-or-newer copy, so writing ours would regress it.  The
          reverse interaction — a delayed full rewrite landing after a
          later-serialized fast save — is handled in
          ``_persist_routing_data``, which folds fast records numbered
          above its snapshot into the rewrite.  An older snapshot can
          therefore never overwrite a newer one, in either direction.

        - No DB, or a failed upsert, falls back to the full rewrite so
          DB-less installs keep sessions.json — their primary store —
          durable every turn.

        ``entry_data`` lets a failure-atomic metadata transition persist a
        candidate before publishing it to the live entry.  Its full-save
        fallback carries the same candidate instead of re-snapshotting the
        unchanged live value.
        """
        def _capture() -> Optional[tuple[str, int, Optional[dict[str, Any]]]]:
            entry = self._entries.get(session_key)
            if entry is None:
                return None
            serialized_entry = (
                dict(entry_data) if entry_data is not None else entry.to_dict()
            )
            entry_json = json.dumps(serialized_entry)
            revision = self._next_routing_generation_locked()
            self._register_pending_routing_generation(revision)
            # Don't eagerly build the O(n) full snapshot — only the candidate
            # is needed for the DB upsert.  The fallback is deferred to the
            # except branch below where it's actually used.
            return entry_json, revision, serialized_entry if entry_data is not None else None

        if lock_held:
            captured = _capture()
        else:
            with self._lock:
                captured = _capture()
        if captured is None:
            return
        entry_json, revision, candidate_entry = captured
        try:
            _db = getattr(self, "_routing_db", None)
            saver = getattr(_db, "save_gateway_routing_entry", None) if _db else None
            if callable(saver):
                save_lock = getattr(self, "_save_lock", None)
                if save_lock is None:
                    save_lock = threading.Lock()
                    self._save_lock = save_lock
                try:
                    with save_lock:
                        if getattr(self, "_persisted_routing_generation", 0) >= revision:
                            return
                        fast_persisted = getattr(self, "_fast_persisted_entries", None)
                        if fast_persisted is None:
                            fast_persisted = {}
                            self._fast_persisted_entries = fast_persisted
                        persisted = fast_persisted.get(session_key)
                        if persisted is not None and persisted[0] >= revision:
                            return
                        saver(session_key, entry_json, scope=self._routing_scope())
                        fast_persisted[session_key] = (revision, entry_json)
                    return
                except Exception as exc:
                    logger.warning(
                        "gateway.session: single-entry routing save failed for %r "
                        "(%s); falling back to full index rewrite",
                        session_key, exc,
                    )
            if candidate_entry is not None:
                # DB upsert failed (or no DB): build the full snapshot now, carrying
                # the candidate entry so the fallback persists the intended
                # transition rather than re-snapshotting the unchanged live value.
                if lock_held:
                    # Caller already holds _lock — build snapshot in-place.
                    fallback_data: dict[str, Any] = {
                        key: current.to_dict()
                        for key, current in self._entries.items()
                    }
                else:
                    with self._lock:
                        fallback_data = {
                            key: current.to_dict()
                            for key, current in self._entries.items()
                        }
                fallback_data[session_key] = candidate_entry
                self._persist_routing_data(
                    fallback_data,
                    revision,
                    reason="single_entry_fallback",
                )
            else:
                self._save_entries(reason="single_entry_fallback")
        finally:
            self._unregister_pending_routing_generation(revision)
