"""Publication failures preserve compensation and observable tracebacks."""
import logging

from hermes_cli import _launchers
import pytest

from tests.hermes_cli import test_native_launcher_transaction as contracts


@pytest.fixture
def runtime(tmp_path, monkeypatch, native_supplier):
    return contracts.runtime.__wrapped__(tmp_path, monkeypatch, native_supplier)


def test_maker_exception_is_logged_and_fails_closed(runtime, monkeypatch, caplog):
    root, python, _supplier, out = runtime
    class BrokenMaker:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('supplier generation failed')
    monkeypatch.setattr(_launchers, '_store_script_maker', lambda python: BrokenMaker)
    with caplog.at_level(logging.ERROR):
        assert _launchers.mint_launcher('hermes', root, out, python, None) is None
    assert any(record.exc_info and 'native launcher generation' in record.message for record in caplog.records)


def test_publication_exception_is_logged_and_compensated(runtime, monkeypatch, caplog):
    root, _python, _supplier, out = runtime
    def fail(*args):
        raise RuntimeError('publication failed')
    monkeypatch.setattr(_launchers, 'mint_launcher', fail)
    with caplog.at_level(logging.ERROR):
        assert _launchers.ensure_install_launchers(root, out) == []
    assert any(record.exc_info and 'native launcher publication' in record.message for record in caplog.records)
