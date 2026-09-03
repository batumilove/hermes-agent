from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE2_RUN = REPO_ROOT / "scripts" / "sandbox" / "stage2-run.sh"


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
