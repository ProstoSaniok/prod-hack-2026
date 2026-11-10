"""Local-only mock Sage gateway and application metrics endpoint for smoke tests."""

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

STATE = {
    "metrics": 0,
    "traces": 0,
    "rejected": 0,
    "trace_ids": [],
    "failures": 0,
    "transient_failures": 0,
}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, body=b"", content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/metrics":
            self.reply(
                200,
                (
                    b"# TYPE prodguild_smoke gauge\nprodguild_smoke 1\n"
                    b"# TYPE prodguild_calls_total counter\nprodguild_calls_total 1\n"
                ),
            )
        elif self.path == "/stats":
            with LOCK:
                body = json.dumps(STATE).encode()
            self.reply(200, body, "application/json")
        else:
            self.reply(200)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/fail-next":
            with LOCK:
                STATE["failures"] = 1
            self.reply(200)
        elif self.path == "/api/v1/write":
            if self.headers.get("Authorization") != "JWT test-only":
                with LOCK:
                    STATE["rejected"] += 1
                self.reply(401)
                return
            with LOCK:
                fail = STATE["failures"] > 0
                if fail:
                    STATE["failures"] -= 1
                    STATE["transient_failures"] += 1
            if fail:
                self.reply(503)
                return
            request = Request(
                "http://sage-metrics:8428/api/v1/write",
                data=body,
                headers={
                    key: self.headers[key]
                    for key in ("Content-Type", "Content-Encoding", "X-Prometheus-Remote-Write-Version")
                    if key in self.headers
                },
            )
            with urlopen(request, timeout=10) as response:
                status = response.status
            with LOCK:
                STATE["metrics"] += 1
            self.reply(status)
        elif self.path == "/v1/traces":
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            with LOCK:
                STATE["traces"] += 1
                # The fixture's trace ID must survive Collector forwarding.
                if bytes.fromhex("0123456789abcdef0123456789abcdef") in body:
                    STATE["trace_ids"].append("0123456789abcdef0123456789abcdef")
            self.reply(200, b"", "application/x-protobuf")
        else:
            self.reply(404)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
