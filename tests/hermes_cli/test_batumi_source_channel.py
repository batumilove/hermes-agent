"""The maintained fork source channel follows live; explicit branches remain authoritative."""
from hermes_cli.source_releases import resolve_source_target


def test_main_channel_selects_live_for_maintained_fork():
    target = resolve_source_target("main", repository="batumilove/hermes-agent")
    assert target.branch == "batumi/live"
    assert target.repository == "batumilove/hermes-agent"


def test_other_repositories_keep_the_main_channel():
    for repository in ("NousResearch/hermes-agent", "someone/hermes-agent"):
        assert resolve_source_target("main", repository=repository).branch == "main"
