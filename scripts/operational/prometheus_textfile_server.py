#!/usr/bin/env python3
"""Serve a single Prometheus textfile over HTTP.

Default endpoint: http://100.126.115.9:9104/metrics
Serves no secret values; it only exposes metrics already written by collectors.
"""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
from pathlib import Path

DEFAULT_FILE = Path.home() / ".hermes/state/prometheus/infisical_audit.prom"


class Handler(http.server.BaseHTTPRequestHandler):
    metrics_file: Path = DEFAULT_FILE

    def do_GET(self):  # noqa: N802
        if self.path not in {"/metrics", "/metrics/"}:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found\n")
            return
        try:
            body = self.metrics_file.read_bytes()
        except FileNotFoundError:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"# metrics file missing\n")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Keep systemd journal quiet; Prometheus scrapes every 30-60s.
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.environ.get("PROM_TEXTFILE", str(DEFAULT_FILE)))
    ap.add_argument("--host", default=os.environ.get("PROM_TEXTFILE_HOST", "100.126.115.9"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PROM_TEXTFILE_PORT", "9104")))
    args = ap.parse_args()
    Handler.metrics_file = Path(args.file).expanduser()
    with socketserver.ThreadingTCPServer((args.host, args.port), Handler) as srv:
        srv.daemon_threads = True
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
