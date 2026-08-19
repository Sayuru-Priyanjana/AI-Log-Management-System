#!/usr/bin/env python3
"""
A single parameterizable HTTP service used for all tiers of the shopdemo system
(checkout-api -> payment-api -> payment-db). Standard library only, so pods start
in about a second with no image build and no pip install.

Two things matter about this file:

1. It emits logs in the *final* nested schema the pipeline consumes. Fluent Bit
   only attaches Kubernetes metadata on top. There is no field renaming anywhere
   in the path, which removes an entire class of silent mapping bugs.

2. Every log line is exactly one line. Exceptions are caught and rendered into a
   single record with `error.stack_trace` as an embedded string, so a traceback
   never becomes 40 separate documents.

Faults are injected at runtime through POST /admin/fault. They are deliberately
in-process and reset on restart: a crash-looping container forgetting its fault
is realistic behaviour, not a bug.
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown-service")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")
SERVICE_TIER = os.getenv("SERVICE_TIER", "backend")
PORT = int(os.getenv("PORT", "8080"))
ROUTE = os.getenv("ROUTE", "/api/work")
DEPENDENCY_NAME = os.getenv("DEPENDENCY_NAME", "")
DEPENDENCY_URL = os.getenv("DEPENDENCY_URL", "")
DEPENDENCY_TIMEOUT_S = float(os.getenv("DEPENDENCY_TIMEOUT_S", "2.0"))
BASE_LATENCY_MS = float(os.getenv("BASE_LATENCY_MS", "8"))
BASE_ERROR_RATE = float(os.getenv("BASE_ERROR_RATE", "0.005"))
CRASH_ON_START = os.getenv("CRASH_ON_START", "").lower() in ("1", "true", "yes")

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


# --------------------------------------------------------------------------
# Logging: one JSON object per line, already in the target schema.
# --------------------------------------------------------------------------
_log_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(level: str, message: str, **fields) -> None:
    record = {
        "@timestamp": _now(),
        "log": {"level": level, "message": message},
        "service": {"name": SERVICE_NAME, "version": SERVICE_VERSION, "tier": SERVICE_TIER},
    }
    for key, value in fields.items():
        if value is None:
            continue
        record[key] = value
    line = json.dumps(record, separators=(",", ":"), default=str)
    with _log_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------
# Metrics: a tiny Prometheus registry. Enough for counters and one histogram.
# --------------------------------------------------------------------------
class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: dict[tuple, int] = {}
        self.dependency: dict[tuple, int] = {}
        self.hist_buckets: dict[tuple, list[int]] = {}
        self.hist_sum: dict[tuple, float] = {}
        self.hist_count: dict[tuple, int] = {}
        self.faults_active = 0

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            key = (method, route, str(status))
            self.requests[key] = self.requests.get(key, 0) + 1

            hkey = (method, route)
            buckets = self.hist_buckets.setdefault(hkey, [0] * (len(LATENCY_BUCKETS) + 1))
            for i, edge in enumerate(LATENCY_BUCKETS):
                if seconds <= edge:
                    buckets[i] += 1
            buckets[-1] += 1  # +Inf
            self.hist_sum[hkey] = self.hist_sum.get(hkey, 0.0) + seconds
            self.hist_count[hkey] = self.hist_count.get(hkey, 0) + 1

    def observe_dependency(self, name: str, outcome: str) -> None:
        with self._lock:
            key = (name, outcome)
            self.dependency[key] = self.dependency.get(key, 0) + 1

    def render(self) -> str:
        with self._lock:
            out: list[str] = []
            svc = SERVICE_NAME

            out.append("# HELP http_requests_total Total HTTP requests handled.")
            out.append("# TYPE http_requests_total counter")
            for (method, route, status), value in sorted(self.requests.items()):
                out.append(
                    f'http_requests_total{{service="{svc}",method="{method}",'
                    f'route="{route}",status="{status}"}} {value}'
                )

            out.append("# HELP http_request_duration_seconds Request latency.")
            out.append("# TYPE http_request_duration_seconds histogram")
            for (method, route), buckets in sorted(self.hist_buckets.items()):
                labels = f'service="{svc}",method="{method}",route="{route}"'
                for i, edge in enumerate(LATENCY_BUCKETS):
                    out.append(
                        f'http_request_duration_seconds_bucket{{{labels},le="{edge}"}} {buckets[i]}'
                    )
                out.append(
                    f'http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {buckets[-1]}'
                )
                out.append(
                    f"http_request_duration_seconds_sum{{{labels}}} "
                    f"{self.hist_sum.get((method, route), 0.0):.6f}"
                )
                out.append(
                    f"http_request_duration_seconds_count{{{labels}}} "
                    f"{self.hist_count.get((method, route), 0)}"
                )

            out.append("# HELP app_dependency_requests_total Calls to downstream dependencies.")
            out.append("# TYPE app_dependency_requests_total counter")
            for (name, outcome), value in sorted(self.dependency.items()):
                out.append(
                    f'app_dependency_requests_total{{service="{svc}",'
                    f'dependency="{name}",outcome="{outcome}"}} {value}'
                )

            out.append("# HELP app_faults_active Number of fault modes currently enabled.")
            out.append("# TYPE app_faults_active gauge")
            out.append(f'app_faults_active{{service="{svc}"}} {self.faults_active}')

            out.append("# HELP app_info Static service metadata.")
            out.append("# TYPE app_info gauge")
            out.append(f'app_info{{service="{svc}",version="{SERVICE_VERSION}"}} 1')
            return "\n".join(out) + "\n"


METRICS = Metrics()


# --------------------------------------------------------------------------
# Fault state
# --------------------------------------------------------------------------
class Faults:
    DEFAULTS: dict[str, object] = {
        "error_rate": 0.0,     # extra probability of a 500
        "latency_ms": 0.0,     # extra latency per request
        "cpu_burn_ms": 0.0,    # busy-wait per request
        "unhealthy": False,    # /health returns 503
        "mem_leak_mb": 0.0,    # target leak size; grows ~10 MB/s until reached
        "refuse": False,       # every request returns 503 immediately
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = dict(self.DEFAULTS)
        self._leak: list[bytearray] = []
        threading.Thread(target=self._leak_loop, daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def update(self, changes: dict) -> dict:
        applied = {}
        with self._lock:
            for key, value in changes.items():
                if key not in self.DEFAULTS:
                    continue
                if isinstance(self.DEFAULTS[key], bool):
                    self.state[key] = bool(value)
                else:
                    self.state[key] = float(value)
                applied[key] = self.state[key]
            if changes.get("mem_leak_mb") == 0:
                self._leak.clear()
            METRICS.faults_active = sum(
                1 for k, v in self.state.items() if v != self.DEFAULTS[k]
            )
        return applied

    def reset(self) -> None:
        with self._lock:
            self.state = dict(self.DEFAULTS)
            self._leak.clear()
            METRICS.faults_active = 0

    def _leak_loop(self) -> None:
        # Allocates in 10 MB steps so the memory curve is a visible ramp in
        # Prometheus rather than a single instantaneous jump.
        while True:
            target_mb = float(self.snapshot()["mem_leak_mb"])
            current_mb = len(self._leak) * 10
            if target_mb > current_mb:
                try:
                    self._leak.append(bytearray(10 * 1024 * 1024))
                except MemoryError:
                    emit("FATAL", "Out of memory while allocating",
                         error={"type": "MemoryError"})
            time.sleep(1.0)


FAULTS = Faults()


# --------------------------------------------------------------------------
# Dependency call
# --------------------------------------------------------------------------
def call_dependency(trace_id: str) -> tuple[bool, str | None, float]:
    """Returns (ok, error_type, elapsed_seconds)."""
    if not DEPENDENCY_URL:
        return True, None, 0.0

    started = time.time()
    request = urllib.request.Request(
        DEPENDENCY_URL, method="POST", data=b"{}",
        headers={"Content-Type": "application/json", "X-Trace-Id": trace_id},
    )
    try:
        with urllib.request.urlopen(request, timeout=DEPENDENCY_TIMEOUT_S) as response:
            response.read()
            elapsed = time.time() - started
            if response.status >= 500:
                METRICS.observe_dependency(DEPENDENCY_NAME, "failure")
                return False, "DependencyServerError", elapsed
            METRICS.observe_dependency(DEPENDENCY_NAME, "success")
            return True, None, elapsed
    except urllib.error.HTTPError as exc:
        METRICS.observe_dependency(DEPENDENCY_NAME, "failure")
        return False, f"DependencyHttp{exc.code}", time.time() - started
    except urllib.error.URLError as exc:
        METRICS.observe_dependency(DEPENDENCY_NAME, "unreachable")
        reason = str(getattr(exc, "reason", exc))
        kind = "DependencyTimeout" if "timed out" in reason.lower() else "DependencyUnreachable"
        return False, kind, time.time() - started
    except Exception:
        METRICS.observe_dependency(DEPENDENCY_NAME, "failure")
        return False, "DependencyError", time.time() - started


def burn_cpu(milliseconds: float) -> None:
    deadline = time.time() + milliseconds / 1000.0
    while time.time() < deadline:
        pass


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence the default stderr access log
        return

    def _respond(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/metrics":
            self._respond(200, METRICS.render().encode(), "text/plain; version=0.0.4")
            return
        if self.path == "/health":
            if FAULTS.snapshot()["unhealthy"]:
                self._respond(503, b'{"status":"unhealthy"}')
            else:
                self._respond(200, b'{"status":"ok"}')
            return
        if self.path == "/admin/fault":
            self._respond(200, json.dumps(FAULTS.snapshot()).encode())
            return
        self._respond(404, b'{"error":"not found"}')

    # -- POST --------------------------------------------------------------
    def do_POST(self) -> None:
        if self.path == "/admin/fault":
            payload = self._read_json()
            if payload.get("reset"):
                FAULTS.reset()
                emit("WARN", "Fault injection reset", event={"category": "admin", "action": "fault.reset"})
            else:
                applied = FAULTS.update(payload)
                emit("WARN", f"Fault injection updated: {applied}",
                     event={"category": "admin", "action": "fault.update", "outcome": "success"})
            self._respond(200, json.dumps(FAULTS.snapshot()).encode())
            return

        if self.path != ROUTE:
            self._respond(404, b'{"error":"not found"}')
            return

        self._handle_work()

    def _handle_work(self) -> None:
        started = time.time()
        trace_id = self.headers.get("X-Trace-Id") or f"tr-{random.randint(10**11, 10**12 - 1):x}"
        request_id = f"rq-{random.randint(10**11, 10**12 - 1):x}"
        faults = FAULTS.snapshot()

        status = 200
        level = "INFO"
        message = f"{SERVICE_NAME} request completed"
        error: dict | None = None
        dependency: dict | None = None

        try:
            if faults["refuse"]:
                status, level = 503, "ERROR"
                message = f"{SERVICE_NAME} is refusing requests"
                error = {"type": "ServiceRefusing", "message": message}
            else:
                if faults["cpu_burn_ms"]:
                    burn_cpu(float(faults["cpu_burn_ms"]))
                time.sleep((BASE_LATENCY_MS + float(faults["latency_ms"])) / 1000.0)

                if DEPENDENCY_URL:
                    ok, error_type, elapsed = call_dependency(trace_id)
                    dependency = {
                        "name": DEPENDENCY_NAME,
                        "outcome": "success" if ok else "failure",
                        "duration_ms": round(elapsed * 1000, 2),
                    }
                    if not ok:
                        status, level = 502, "ERROR"
                        message = f"Upstream dependency {DEPENDENCY_NAME} failed: {error_type}"
                        error = {"type": error_type, "message": message}

                if status == 200:
                    roll = random.random()
                    if roll < BASE_ERROR_RATE + float(faults["error_rate"]):
                        status, level = 500, "ERROR"
                        message = f"{SERVICE_NAME} failed to process the request"
                        error = {"type": "InternalProcessingError", "message": message}

        except Exception as exc:  # one line, never a shredded traceback
            status, level = 500, "ERROR"
            message = f"Unhandled exception in {SERVICE_NAME}: {exc}"
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "stack_trace": "".join(traceback.format_exc()).replace("\n", " | "),
            }

        duration = time.time() - started
        body = json.dumps({"status": "ok" if status == 200 else "error", "trace_id": trace_id})
        self._respond(status, body.encode())

        METRICS.observe("POST", ROUTE, status, duration)
        if level == "INFO" and duration * 1000 > (BASE_LATENCY_MS * 20 + 250):
            level, message = "WARN", f"{SERVICE_NAME} request was slow"

        emit(
            level, message,
            event={
                "category": "http",
                "action": f"{SERVICE_NAME}.request",
                "outcome": "success" if status < 400 else "failure",
            },
            http={
                "method": "POST", "route": ROUTE,
                "status_code": status,
                "response_time_ms": round(duration * 1000, 2),
            },
            error=error,
            dependency=dependency,
            trace={"id": trace_id},
            request={"id": request_id},
        )


def main() -> None:
    if CRASH_ON_START:
        emit("FATAL", f"{SERVICE_NAME} failed to initialise: configuration is invalid",
             event={"category": "lifecycle", "action": "startup", "outcome": "failure"},
             error={"type": "StartupConfigurationError"})
        time.sleep(1)
        sys.exit(1)

    emit("INFO", f"{SERVICE_NAME} listening on :{PORT}",
         event={"category": "lifecycle", "action": "startup", "outcome": "success"},
         http={"route": ROUTE})

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        emit("INFO", f"{SERVICE_NAME} shutting down",
             event={"category": "lifecycle", "action": "shutdown", "outcome": "success"})


if __name__ == "__main__":
    main()
