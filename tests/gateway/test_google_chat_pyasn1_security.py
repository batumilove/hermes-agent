"""A previously installed vulnerable ASN.1 decoder must trigger PM repair."""

from plugins.platforms.google_chat import oauth


def test_previously_fixed_decoder_is_now_stale(monkeypatch):
    installed = dict(spec.split("==") for spec in oauth._REQUIRED_PACKAGES)
    installed["pyasn1"] = "0.6.4"
    monkeypatch.setattr(oauth, "_distribution_version", installed.__getitem__)

    assert oauth._missing_required_packages() == ["pyasn1==0.6.5"]
