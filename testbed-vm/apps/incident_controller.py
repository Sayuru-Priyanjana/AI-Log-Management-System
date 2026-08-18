#!/usr/bin/env python3
"""
Incident controller: injects reproducible failures into the shopdemo system.

Exposed on NodePort 30099 so the evaluation harness in WSL can drive scenarios
over plain HTTP — no SSH keys, no kubectl on the WSL side, no Windows/WSL path
translation.

The scenario catalogue here is the single source of truth for what each incident
is *supposed* to produce. The evaluation harness reads `expected_signals` and
`expected_cause` straight off GET /incidents, so the ground truth cannot drift
away from the injector.

Two mechanisms:
  fault / rate  -> HTTP call to the app's own admin endpoint. Instant, no restart.
  scale / patch -> Kubernetes API. Causes a real rollout, which is the point for
                   crashloop, OOM and scheduling scenarios.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE = os.getenv("TARGET_NAMESPACE", "shopdemo")
PORT = int(os.getenv("PORT", "8080"))

_lock = threading.Lock()
_active: dict[str, str] = {}   # scenario id -> ISO start time


def log(level: str, message: str, **fields) -> None:
    record = {
        "@timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "log": {"level": level, "message": message},
        "service": {"name": "incident-controller", "version": "1.0.0", "tier": "platform"},
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------
# Scenario catalogue
# --------------------------------------------------------------------------
# `settle_seconds` is how long the incident needs to develop before an
# investigation can reasonably be expected to see it — a crashloop needs several
# restart backoffs, an error-rate change is visible almost immediately.
SCENARIOS: dict[str, dict] = {
    "dependency-outage": {
        "title": "payment-db scaled to zero",
        "description": "The database tier disappears. payment-api times out, checkout-api "
                       "returns 502. The root cause is one hop below the reported symptom.",
        "settle_seconds": 90,
        "expected_signals": ["DEPENDENCY_UNAVAILABLE", "ERROR_RATE_SPIKE", "NEW_ERROR_PATTERN"],
        "expected_cause": "dependency_failure",
        "expected_service": "payment-db",
        "start": [{"op": "scale", "deployment": "payment-db", "replicas": 0}],
        "stop":  [{"op": "scale", "deployment": "payment-db", "replicas": 1}],
    },
    "db-latency": {
        "title": "payment-db responds slowly",
        "description": "1.5s added latency in the database tier propagates up as "
                       "latency degradation, then timeouts.",
        "settle_seconds": 120,
        "expected_signals": ["LATENCY_DEGRADATION"],
        "expected_cause": "dependency_degradation",
        "expected_service": "payment-db",
        "start": [{"op": "fault", "service": "payment-db", "fault": {"latency_ms": 1500}}],
        "stop":  [{"op": "fault", "service": "payment-db", "fault": {"reset": True}}],
    },
    "payment-5xx": {
        "title": "payment-api returns 500s",
        "description": "60% of payment requests fail internally with no infrastructure "
                       "signal. Tests that the pipeline does not invent a resource cause.",
        "settle_seconds": 90,
        "expected_signals": ["HTTP_5XX_BURST", "ERROR_RATE_SPIKE"],
        "expected_cause": "application_fault",
        "expected_service": "payment-api",
        "start": [{"op": "fault", "service": "payment-api", "fault": {"error_rate": 0.6}}],
        "stop":  [{"op": "fault", "service": "payment-api", "fault": {"reset": True}}],
    },
    "memory-leak-oom": {
        "title": "payment-api leaks memory until OOMKilled",
        "description": "Memory ramps past the 256Mi limit; the kubelet OOM-kills the "
                       "container and it restarts. Tests ordered causal chains.",
        "settle_seconds": 180,
        "expected_signals": ["MEMORY_PRESSURE", "OOM_KILL", "POD_RESTART"],
        "expected_cause": "resource_exhaustion",
        "expected_service": "payment-api",
        "start": [{"op": "fault", "service": "payment-api", "fault": {"mem_leak_mb": 600}}],
        "stop":  [{"op": "fault", "service": "payment-api", "fault": {"reset": True}},
                  {"op": "restart", "deployment": "payment-api"}],
    },
    "cpu-saturation": {
        "title": "payment-api saturates its CPU limit",
        "description": "300ms of CPU burn per request against a 300m limit produces "
                       "throttling and latency degradation without any errors.",
        "settle_seconds": 150,
        "expected_signals": ["CPU_SATURATION", "CPU_THROTTLING", "LATENCY_DEGRADATION"],
        "expected_cause": "resource_saturation",
        "expected_service": "payment-api",
        "start": [{"op": "fault", "service": "payment-api", "fault": {"cpu_burn_ms": 300}},
                  {"op": "rate", "rps": 6}],
        "stop":  [{"op": "fault", "service": "payment-api", "fault": {"reset": True}},
                  {"op": "rate", "rps": 2}],
    },
    "crashloop": {
        "title": "payment-api crashes on startup",
        "description": "The container exits 1 immediately, producing CrashLoopBackOff "
                       "and a FATAL startup log before each exit.",
        "settle_seconds": 180,
        "expected_signals": ["CRASHLOOP", "POD_RESTART", "DEPENDENCY_UNAVAILABLE"],
        "expected_cause": "startup_failure",
        "expected_service": "payment-api",
        "start": [{"op": "env", "deployment": "payment-api", "container": "app",
                   "env": {"CRASH_ON_START": "true"}}],
        "stop":  [{"op": "env", "deployment": "payment-api", "container": "app",
                   "env": {"CRASH_ON_START": "false"}}],
    },
    "readiness-failure": {
        "title": "payment-api fails its readiness probe",
        "description": "The pod stays running but leaves the Service endpoints. "
                       "Unhealthy events appear with no restarts — the distinguishing "
                       "feature against crashloop.",
        "settle_seconds": 120,
        "expected_signals": ["READINESS_FAILURE"],
        "expected_cause": "readiness_failure",
        "expected_service": "payment-api",
        "start": [{"op": "fault", "service": "payment-api", "fault": {"unhealthy": True}}],
        "stop":  [{"op": "fault", "service": "payment-api", "fault": {"reset": True}}],
    },
    "scheduling-failure": {
        "title": "payment-api cannot be scheduled",
        "description": "A CPU request of 8 cores on a 2-core node leaves the new pod "
                       "Pending with FailedScheduling.",
        "settle_seconds": 120,
        "expected_signals": ["SCHEDULING_FAILURE", "DEPLOYMENT_CHANGE"],
        "expected_cause": "scheduling_failure",
        "expected_service": "payment-api",
        "start": [{"op": "resources", "deployment": "payment-api", "container": "app",
                   "requests": {"cpu": "8"}}],
        "stop":  [{"op": "resources", "deployment": "payment-api", "container": "app",
                   "requests": {"cpu": "50m"}}],
    },
    "bad-deploy": {
        "title": "a bad version of payment-api is rolled out",
        "description": "A deployment change immediately precedes a 50% error rate. "
                       "Tests whether the change is identified as the cause rather "
                       "than the errors it produced.",
        "settle_seconds": 120,
        "expected_signals": ["DEPLOYMENT_CHANGE", "ERROR_RATE_SPIKE", "HTTP_5XX_BURST"],
        "expected_cause": "change_induced",
        "expected_service": "payment-api",
        "start": [{"op": "env", "deployment": "payment-api", "container": "app",
                   "env": {"SERVICE_VERSION": "2.1.0", "BASE_ERROR_RATE": "0.5"}}],
        "stop":  [{"op": "env", "deployment": "payment-api", "container": "app",
                   "env": {"SERVICE_VERSION": "1.0.0", "BASE_ERROR_RATE": "0.005"}}],
    },
    "traffic-surge": {
        "title": "checkout traffic increases 10x",
        "description": "Load rises with no fault injected anywhere. The correct answer "
                       "is load, not a broken service — tests against false positives.",
        "settle_seconds": 150,
        "expected_signals": ["TRAFFIC_SURGE", "LATENCY_DEGRADATION"],
        "expected_cause": "load_increase",
        "expected_service": "checkout-api",
        # 15 rps rather than something extreme: enough to move latency clearly,
        # not so much that the tiers start timing out and it stops being a pure
        # load scenario.
        "start": [{"op": "rate", "rps": 15}],
        "stop":  [{"op": "rate", "rps": 2}],
    },
}


# --------------------------------------------------------------------------
# Kubernetes helpers
# --------------------------------------------------------------------------
def _k8s(method: str, path: str, body: dict | None = None, content_type: str | None = None) -> dict:
    with open(TOKEN_PATH, encoding="utf-8") as handle:
        token = handle.read().strip()
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = content_type or "application/json"
    request = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def op_scale(step: dict) -> str:
    path = f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{step['deployment']}/scale"
    _k8s("PATCH", path, {"spec": {"replicas": step["replicas"]}}, "application/merge-patch+json")
    return f"scaled {step['deployment']} to {step['replicas']}"


def op_env(step: dict) -> str:
    env = [{"name": k, "value": v} for k, v in step["env"].items()]
    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": step["container"], "env": env}
    ]}}}}
    _k8s("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{step['deployment']}",
         patch, "application/strategic-merge-patch+json")
    return f"patched env on {step['deployment']}: {list(step['env'])}"


def op_resources(step: dict) -> str:
    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": step["container"], "resources": {"requests": step["requests"]}}
    ]}}}}
    _k8s("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{step['deployment']}",
         patch, "application/strategic-merge-patch+json")
    return f"patched resources on {step['deployment']}: {step['requests']}"


def op_restart(step: dict) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    patch = {"spec": {"template": {"metadata": {"annotations": {
        "logintel.io/restartedAt": stamp
    }}}}}
    _k8s("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{step['deployment']}",
         patch, "application/strategic-merge-patch+json")
    return f"restarted {step['deployment']}"


def _post_json(url: str, payload: dict) -> str:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()[:200]


def op_fault(step: dict) -> str:
    url = f"http://{step['service']}.{NAMESPACE}.svc:8080/admin/fault"
    return f"fault on {step['service']}: {_post_json(url, step['fault'])}"


def op_rate(step: dict) -> str:
    url = f"http://loadgen.{NAMESPACE}.svc:8080/admin/rate"
    return f"loadgen rate: {_post_json(url, {'rps': step['rps']})}"


OPS = {"scale": op_scale, "env": op_env, "resources": op_resources,
       "restart": op_restart, "fault": op_fault, "rate": op_rate}


def run_steps(steps: list[dict]) -> list[str]:
    results = []
    for step in steps:
        handler = OPS.get(step["op"])
        if not handler:
            results.append(f"unknown op {step['op']}")
            continue
        try:
            results.append(handler(step))
        except Exception as exc:
            results.append(f"FAILED {step['op']}: {exc}")
            log("ERROR", f"Incident step failed: {step['op']}: {exc}",
                error={"type": type(exc).__name__, "message": str(exc)})
    return results


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/incidents", "/health"):
            with _lock:
                active = dict(_active)
            self._respond(200, {
                "namespace": NAMESPACE,
                "active": active,
                "scenarios": {
                    key: {k: v for k, v in value.items() if k not in ("start", "stop")}
                    for key, value in SCENARIOS.items()
                },
            })
            return
        self._respond(404, {"error": "not found",
                            "hint": "GET /incidents, POST /incidents/{id}/start|stop"})

    def do_POST(self) -> None:
        parts = [p for p in self.path.split("/") if p]

        if parts == ["incidents", "reset-all"]:
            results = []
            with _lock:
                active = list(_active)
            for scenario_id in active:
                results += run_steps(SCENARIOS[scenario_id]["stop"])
            with _lock:
                _active.clear()
            # Belt and braces: clear faults even on services no scenario touched.
            for service in ("payment-db", "payment-api", "checkout-api"):
                try:
                    op_fault({"service": service, "fault": {"reset": True}})
                except Exception:
                    pass
            try:
                op_rate({"rps": 2})
            except Exception:
                pass
            log("WARN", "All incidents reset",
                event={"category": "incident", "action": "reset-all", "outcome": "success"})
            self._respond(200, {"reset": True, "results": results})
            return

        if len(parts) == 3 and parts[0] == "incidents" and parts[2] in ("start", "stop"):
            scenario_id, action = parts[1], parts[2]
            scenario = SCENARIOS.get(scenario_id)
            if not scenario:
                self._respond(404, {"error": f"unknown scenario '{scenario_id}'",
                                    "available": sorted(SCENARIOS)})
                return

            started_at = datetime.now(timezone.utc).isoformat()
            results = run_steps(scenario[action])
            with _lock:
                if action == "start":
                    _active[scenario_id] = started_at
                else:
                    _active.pop(scenario_id, None)

            log("WARN", f"Incident '{scenario_id}' {action}: {scenario['title']}",
                event={"category": "incident", "action": action, "outcome": "success"},
                incident={"id": scenario_id, "title": scenario["title"]})

            self._respond(200, {
                "scenario": scenario_id,
                "action": action,
                "at": started_at,
                "settle_seconds": scenario["settle_seconds"],
                "expected_signals": scenario["expected_signals"],
                "expected_cause": scenario["expected_cause"],
                "results": results,
            })
            return

        self._respond(404, {"error": "not found"})


def main() -> None:
    log("INFO", f"incident-controller listening on :{PORT} for namespace {NAMESPACE}",
        event={"category": "lifecycle", "action": "startup", "outcome": "success"},
        incident={"scenarios": len(SCENARIOS)})
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
