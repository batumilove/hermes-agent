"""Fork policy contract for outbound Codex request identity."""


def test_codex_headers_do_not_claim_hermes_identity_without_opt_in() -> None:
    from agent.auxiliary_client import _codex_cloudflare_headers

    headers = _codex_cloudflare_headers("not-a-jwt")

    assert headers["originator"] == "codex_cli_rs"
    assert not headers["User-Agent"].startswith("HermesAgent/")
    assert "Hermes" not in headers["User-Agent"]
