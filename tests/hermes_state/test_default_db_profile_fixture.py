"""Isolated test defaults still route by profile and retain explicit DB pins."""

import hermes_state
from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override


def test_default_session_db_follows_context_profile_home():
    profile = get_hermes_home() / "profiles" / "routed"
    profile.mkdir(parents=True)
    token = set_hermes_home_override(profile)
    try:
        db = hermes_state.SessionDB()
        try:
            assert db.db_path == profile / "state.db"
            assert db.db_path.is_file()
        finally:
            db.close()
    finally:
        reset_hermes_home_override(token)


def test_explicit_default_db_pin_wins_over_context_profile_home(monkeypatch):
    profile = get_hermes_home() / "profiles" / "pinned"
    profile.mkdir(parents=True)
    pin = profile / "explicit.db"
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", pin)
    token = set_hermes_home_override(profile)
    try:
        db = hermes_state.SessionDB()
        try:
            assert db.db_path == pin
            assert pin.is_file()
            assert not (profile / "state.db").exists()
        finally:
            db.close()
    finally:
        reset_hermes_home_override(token)
