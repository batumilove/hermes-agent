"""Stable side-effect verdicts survive transcript insertion, replay and replacement."""

import pytest

from hermes_state import SessionDB


@pytest.mark.parametrize("verdict", [True, False, None])
def test_append_and_replace_preserve_side_effect_evidence(tmp_path, verdict):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("s", "cli")
        db.append_message("s", "user", "perform action")
        db.append_message(
            "s",
            "assistant",
            "action completed",
            _side_effect_evidence_succeeded=verdict,
        )
        for replace in [False, True]:
            if replace:
                db.replace_messages(
                    "s",
                    db.get_messages_as_conversation(
                        "s", include_ancestors=False, repair_alternation=False
                    ),
                )
            history = db.get_messages_as_conversation(
                "s", include_ancestors=False, repair_alternation=False
            )
            if verdict is None:
                assert "_side_effect_evidence_succeeded" not in history[-1]
            else:
                assert history[-1]["_side_effect_evidence_succeeded"] is verdict
