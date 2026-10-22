"""Chronos JWT verification must bound unknown-kid refreshes and retain rotation.

GHSA-2gx3-rcp4-g85q: cached PyJWKClient instances alone did not bound
unauthenticated refresh requests before PyJWT 2.14.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from plugins.cron_providers.chronos import verify


def test_chronos_unknown_kids_do_not_amplify_jwks_fetches_and_rotation_recovers(monkeypatch):
    original_clock = time.monotonic
    elapsed = [0.0]
    clock = SimpleNamespace(monotonic=lambda: original_clock() + elapsed[0])
    monkeypatch.setattr(jwt.jwks_client, "time", clock, raising=False)
    monkeypatch.setattr(jwt.jwk_set_cache, "time", clock)
    monkeypatch.setattr(jwt.api_jwk, "time", clock)
    monkeypatch.setattr(verify, "_JWK_CLIENTS", {})
    original = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public(key, kid):
        return {**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": kid}

    state = {"keys": [public(original, "original")], "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            body = json.dumps({"keys": state["keys"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    now = int(time.time())
    claims = {"aud": "agent:security-test", "purpose": "cron_fire", "exp": now + 300}

    def token(key, kid):
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/jwks"

        def check(value):
            return verify.verify_nas_fire_token(
                token=value, expected_audience=claims["aud"], jwks_or_key=url)

        try:
            assert check(token(original, "original")) == claims
            warm_hits = state["hits"]
            for kid in ("missing-a", "missing-b", "missing-a", "missing-c"):
                assert check(token(original, kid)) is None
            assert state["hits"] - warm_hits <= 1
            assert check(token(original, "original")) == claims
            assert state["hits"] - warm_hits <= 1

            # Advance only the dependency's cache clock, leaving token expiry
            # and the server/runner deadlines on their real clocks.
            state["keys"] = [public(rotated, "rotated")]
            elapsed[0] = 360.0
            before_rotation = state["hits"]
            assert check(token(rotated, "rotated")) == claims
            assert state["hits"] == before_rotation + 1
            assert check(token(rotated, "rotated")) == claims
            assert state["hits"] == before_rotation + 1
        finally:
            server.shutdown()
            thread.join(timeout=5)
