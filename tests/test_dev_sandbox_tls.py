import importlib.util
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE2_RUN = REPO_ROOT / "scripts" / "sandbox" / "stage2-run.sh"
INSTALL_E2E = REPO_ROOT / "tests" / "install" / "install-update-e2e.sh"


def test_intercepted_https_clients_trust_sandbox_ca():
    script = STAGE2_RUN.read_text(encoding="utf-8")
    client_ca_vars = {
        name: value
        for name, value in re.findall(
            r"--setenv (CURL_CA_BUNDLE|SSL_CERT_FILE|GIT_SSL_CAINFO|NODE_EXTRA_CA_CERTS) (\S+)",
            script,
        )
    }

    assert client_ca_vars == {
        "CURL_CA_BUNDLE": "/work/certs/ca.pem",
        "SSL_CERT_FILE": "/work/certs/ca.pem",
        "GIT_SSL_CAINFO": "/work/certs/ca.pem",
        "NODE_EXTRA_CA_CERTS": "/work/certs/ca.pem",
    }
    assert (
        "python3 /work/proxy.py /work/http /work/certs /work/certs/real-ca.pem"
        in script
    )


def test_proxy_forces_one_unambiguous_connection_close(monkeypatch, tmp_path):
    proxy_path = REPO_ROOT / "scripts" / "sandbox" / "proxy.py"
    spec = importlib.util.spec_from_file_location("sandbox_proxy_test", proxy_path)
    assert spec is not None and spec.loader is not None
    proxy = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(
        sys,
        "argv",
        ["proxy.py", str(tmp_path), str(tmp_path), str(tmp_path / "ca.pem")],
    )
    spec.loader.exec_module(proxy)

    request = (
        b"GET /package HTTP/1.1\r\n"
        b"Host: registry.npmjs.org\r\n"
        b"Connection: keep-alive\r\n"
        b"Proxy-Connection: keep-alive\r\n\r\n"
    )
    rewritten = proxy.close_request(request)
    header_lines = rewritten.partition(b"\r\n\r\n")[0].split(b"\r\n")

    assert [
        line for line in header_lines if line.lower().startswith(b"connection:")
    ] == [b"Connection: close"]
    assert not any(
        line.lower().startswith(b"proxy-connection:") for line in header_lines
    )


def test_installer_sandbox_does_not_force_host_node_headers():
    script = STAGE2_RUN.read_text(encoding="utf-8")

    assert "npm_config_nodedir" not in script


def test_install_e2e_artifacts_include_npm_debug_logs():
    script = INSTALL_E2E.read_text(encoding="utf-8")

    assert 'npm_src="$SANDBOX_ROOT/home/.npm/_logs"' in script
    assert 'cp -a "$npm_src/." "$dest/npm/"' in script
