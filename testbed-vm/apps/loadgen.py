#!/usr/bin/env python3
"""
Traffic generator for the shopdemo system. Standard library only.

Drives a steady, configurable request rate at checkout-api so the pipeline always
has a baseline to compare an incident against. A system with no traffic has no
error *rate*, only error counts, and rate-based signals cannot be evaluated.

Rate is adjustable at runtime via POST /admin/rate {"rps": 8} so a traffic-surge
incident does not require a redeploy.
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET_URL = os.getenv("TARGET_URL", "http://checkout-api.shopdemo.svc:8080/api/checkout")
RPS = float(os.getenv("RPS", "2.0"))
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8080"))
SERVICE_NAME = os.getenv("SERVICE_NAME", "loadgen")

_state = {"rps": RPS}
_lock = threading.Lock()
_stats = {"sent": 0, "ok": 0, "failed": 0}


def emit(level: str, message: str, **fields) -> None:
    record = {
        "@timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "log": {"level": level, "message": message},
        "service": {"name": SERVICE_NAME, "version": "1.0.0", "tier": "loadgen"},
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def worker() -> None:
    while True:
        with _lock:
            rps = max(_state["rps"], 0.01)
        interval = 1.0 / rps
        started = time.time()
        trace_id = f"tr-{random.randint(10**11, 10**12 - 1):x}"
        request = urllib.request.Request(
            TARGET_URL, method="POST", data=b"{}",
            headers={"Content-Type": "application/json", "X-Trace-Id": trace_id},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
                _stats["ok"] += 1
        except urllib.error.HTTPError:
            _stats["failed"] += 1
        except Exception:
            _stats["failed"] += 1
        _stats["sent"] += 1

        elapsed = time.time() - started
        time.sleep(max(0.0, interval - elapsed))


def reporter() -> None:
    # A periodic heartbeat, not a per-request log: loadgen noise would otherwise
    # dominate the very index the pipeline is trying to analyse.
    last = dict(_stats)
    while True:
        time.sleep(30)
        delta = {k: _stats[k] - last.get(k, 0) for k in _stats}
        last = dict(_stats)
        with _lock:
            rps = _state["rps"]
        failure_ratio = delta["failed"] / delta["sent"] if delta["sent"] else 0.0
        emit(
            "WARN" if failure_ratio > 0.2 else "INFO",
            f"loadgen sent {delta['sent']} requests, {delta['failed']} failed",
            event={"category": "loadgen", "action": "report",
                   "outcome": "failure" if failure_ratio > 0.2 else "success"},
            loadgen={"rps": rps, "sent": delta["sent"], "failed": delta["failed"],
                     "failure_ratio": round(failure_ratio, 4)},
        )


class Admin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/health", "/admin/rate"):
            with _lock:
                self._respond(200, {"status": "ok", "rps": _state["rps"], **_stats})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/admin/rate":
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}
        if "rps" in payload:
            with _lock:
                _state["rps"] = max(0.01, float(payload["rps"]))
            emit("WARN", f"loadgen rate changed to {_state['rps']} rps",
                 event={"category": "admin", "action": "rate.update", "outcome": "success"})
        with _lock:
            self._respond(200, {"rps": _state["rps"]})


def main() -> None:
    concurrency = int(os.getenv("CONCURRENCY", "2"))
    emit("INFO", f"loadgen starting: {RPS} rps x{concurrency} against {TARGET_URL}",
         event={"category": "lifecycle", "action": "startup", "outcome": "success"})
    for _ in range(concurrency):
        threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=reporter, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", ADMIN_PORT), Admin).serve_forever()


if __name__ == "__main__":
    main()
