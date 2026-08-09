#!/usr/bin/env python3
"""
Ships Kubernetes events to OpenSearch. Standard library only — it talks to the
API server and to OpenSearch over plain HTTP with the service account token, so
the pod needs no image build and no pip install.

Design notes that matter downstream:

* `event.type` keeps the real Kubernetes value (Normal / Warning). The v1
  collector overwrote it with a constant, which made every event look benign.
* `reason` is the primary key for signal detection. `category` and `action` are
  optional enrichment and are omitted entirely when unknown, so they can never
  shadow the reason.
* `count`, `first_timestamp` and `last_timestamp` are all preserved. A single
  Kubernetes event can represent hundreds of occurrences over hours; collapsing
  that to one point in time destroys the ordering the pipeline reasons about.

Events are polled rather than watched, and upserted by UID. Polling is idempotent,
survives API server restarts with no reconnect logic, and event volume is tiny.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://192.168.56.1:9200").rstrip("/")
INDEX_PREFIX = os.getenv("INDEX_PREFIX", "logintel-events")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))
CLUSTER = os.getenv("CLUSTER_NAME", "logintel-testbed")
DEFAULT_SYSTEM_ID = os.getenv("DEFAULT_SYSTEM_ID", "shopdemo")
DEFAULT_SYSTEM_NAME = os.getenv("DEFAULT_SYSTEM_NAME", "Shop Demo")
DEFAULT_ENVIRONMENT = os.getenv("DEFAULT_ENVIRONMENT", "staging")

# Namespaces we care about. Everything else in the cluster is infrastructure noise.
WATCH_NAMESPACES = [
    ns.strip() for ns in os.getenv("WATCH_NAMESPACES", "shopdemo").split(",") if ns.strip()
]

# Reasons that always indicate a problem regardless of the event type field.
CRITICAL_REASONS = {
    "OOMKilling", "OOMKilled", "Evicted", "FailedScheduling", "FailedCreatePodSandBox",
    "NodeNotReady", "SystemOOM", "FailedMount", "FailedAttachVolume",
}
WARNING_REASONS = {
    "BackOff", "Unhealthy", "Failed", "FailedSync", "ImagePullBackOff", "ErrImagePull",
    "Killing", "Preempting", "NodeHasDiskPressure", "NodeHasMemoryPressure", "ProbeWarning",
}


def log(level: str, message: str, **fields) -> None:
    record = {
        "@timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "log": {"level": level, "message": message},
        "service": {"name": "event-collector", "version": "2.0.0", "tier": "platform"},
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def read_token() -> str:
    with open(TOKEN_PATH, encoding="utf-8") as handle:
        return handle.read().strip()


def k8s_get(path: str) -> dict:
    context = ssl.create_default_context(cafile=CA_PATH)
    request = urllib.request.Request(
        f"{API}{path}", headers={"Authorization": f"Bearer {read_token()}"}
    )
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        return json.loads(response.read())


def severity_for(event_type: str, reason: str) -> str:
    if reason in CRITICAL_REASONS:
        return "critical"
    if event_type == "Warning" or reason in WARNING_REASONS:
        return "warning"
    return "info"


def normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    return value if value.endswith("Z") or "+" in value else value + "Z"


def pod_labels_cache() -> dict:
    """Pod labels carry the system identity. Cached per poll; pods are few here."""
    labels: dict[tuple[str, str], dict] = {}
    for namespace in WATCH_NAMESPACES:
        try:
            payload = k8s_get(f"/api/v1/namespaces/{namespace}/pods?limit=500")
        except Exception as exc:
            log("WARN", f"Could not list pods in {namespace}: {exc}")
            continue
        for item in payload.get("items", []):
            meta = item.get("metadata", {})
            labels[(namespace, meta.get("name", ""))] = meta.get("labels", {}) or {}
    return labels


def normalize(event: dict, labels: dict) -> dict | None:
    meta = event.get("metadata", {})
    involved = event.get("involvedObject", {})
    namespace = involved.get("namespace") or meta.get("namespace")
    if namespace not in WATCH_NAMESPACES:
        return None

    uid = meta.get("uid")
    if not uid:
        return None

    reason = event.get("reason") or "Unknown"
    event_type = event.get("type") or "Normal"
    last_ts = normalize_timestamp(
        event.get("lastTimestamp")
        or (event.get("series") or {}).get("lastObservedTime")
        or event.get("eventTime")
        or meta.get("creationTimestamp")
    )
    first_ts = normalize_timestamp(event.get("firstTimestamp") or event.get("eventTime")) or last_ts
    if not last_ts:
        return None

    pod_labels = labels.get((namespace, involved.get("name", "")), {})
    system_id = pod_labels.get("logintel/system-id", DEFAULT_SYSTEM_ID)
    system_name = pod_labels.get("logintel/system-name", DEFAULT_SYSTEM_NAME).replace("_", " ")
    environment = pod_labels.get("logintel/environment", DEFAULT_ENVIRONMENT)

    doc = {
        "@timestamp": last_ts,
        "system": {"id": system_id, "name": system_name},
        "environment": environment,
        "source": {"type": "kubernetes", "collector": "event-collector"},
        "kubernetes": {"cluster": CLUSTER, "namespace": namespace},
        "event": {
            "id": uid,
            "kind": "kubernetes_event",
            "type": event_type,                # Normal | Warning — the real value
            "reason": reason,                  # primary key for signal detection
            "message": event.get("message") or "",
            "severity": severity_for(event_type, reason),
            "count": int(event.get("count") or (event.get("series") or {}).get("count") or 1),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "reporting_component": event.get("reportingComponent")
            or (event.get("source") or {}).get("component"),
        },
        "involved_object": {
            "kind": involved.get("kind"),
            "name": involved.get("name"),
            "uid": involved.get("uid"),
            "field_path": involved.get("fieldPath"),
        },
    }

    kind = (involved.get("kind") or "").lower()
    name = involved.get("name")
    if kind == "pod":
        doc["kubernetes"]["pod"] = {"name": name, "uid": involved.get("uid")}
        field_path = involved.get("fieldPath") or ""
        if "{" in field_path and "}" in field_path:
            container = field_path[field_path.find("{") + 1:field_path.find("}")]
            doc["kubernetes"]["container"] = {"name": container}
        service = pod_labels.get("app")
        if service:
            doc["service"] = {"name": service}
    elif kind:
        doc["kubernetes"][kind] = {"name": name}
        # Deployment/ReplicaSet events matter for change correlation, and their
        # `app` label is what ties them back to a service.
        if pod_labels.get("app"):
            doc["service"] = {"name": pod_labels["app"]}

    host = (event.get("source") or {}).get("host") or event.get("reportingInstance")
    if host:
        doc["kubernetes"].setdefault("node", {"name": host})
    return doc


def bulk_index(docs: list[dict]) -> int:
    if not docs:
        return 0
    index = f"{INDEX_PREFIX}-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"
    lines = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": index, "_id": doc["event"]["id"]}}))
        lines.append(json.dumps(doc, default=str))
    body = ("\n".join(lines) + "\n").encode()

    request = urllib.request.Request(
        f"{OPENSEARCH_URL}/_bulk", data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if result.get("errors"):
        failed = [
            item["index"]["error"]
            for item in result.get("items", [])
            if item.get("index", {}).get("error")
        ]
        log("WARN", f"{len(failed)} events rejected by OpenSearch",
            error={"type": "BulkIndexPartialFailure", "message": json.dumps(failed[:3])})
    return len(docs)


def main() -> None:
    log("INFO", f"event-collector starting; namespaces={WATCH_NAMESPACES} -> {OPENSEARCH_URL}",
        event={"category": "lifecycle", "action": "startup", "outcome": "success"})

    # Re-sending an unchanged event is harmless (same _id, same body) but wasteful.
    seen: dict[str, int] = {}

    while True:
        try:
            labels = pod_labels_cache()
            batch: list[dict] = []
            for namespace in WATCH_NAMESPACES:
                payload = k8s_get(f"/api/v1/namespaces/{namespace}/events?limit=500")
                for raw in payload.get("items", []):
                    doc = normalize(raw, labels)
                    if not doc:
                        continue
                    uid, count = doc["event"]["id"], doc["event"]["count"]
                    if seen.get(uid) == count:
                        continue
                    seen[uid] = count
                    batch.append(doc)

            if batch:
                bulk_index(batch)
                log("INFO", f"Indexed {len(batch)} Kubernetes events",
                    event={"category": "collector", "action": "index", "outcome": "success"},
                    collector={"indexed": len(batch), "tracked": len(seen)})

            if len(seen) > 5000:  # events expire from the API server anyway
                seen.clear()

        except urllib.error.URLError as exc:
            log("ERROR", f"OpenSearch or API server unreachable: {exc}",
                event={"category": "collector", "action": "index", "outcome": "failure"},
                error={"type": "Unreachable", "message": str(exc)})
        except Exception as exc:
            log("ERROR", f"Collector loop failed: {exc}",
                event={"category": "collector", "action": "index", "outcome": "failure"},
                error={"type": type(exc).__name__, "message": str(exc)})

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
