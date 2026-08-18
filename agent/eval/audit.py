"""
Audits persisted investigations for systematic problems.

The evaluation harness scores individual runs against known ground truth. This
looks across every run that has been stored and asks a different question: which
failure modes keep recurring? A verification code that appears on most runs is
usually a bug in the pipeline, not a series of unlucky incidents.

    python -m eval.audit [limit] [since]

`since` is an OpenSearch date expression such as `now-2h`. Use it after changing
the pipeline: mixing runs from before and after a fix makes the "fires on most
runs" heuristic report the old behaviour as if it were current.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from app.config import settings
from app.sources.opensearch import OpenSearchClient


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    since = sys.argv[2] if len(sys.argv) > 2 else None
    query: dict = {"match_all": {}}
    if since:
        query = {"range": {"created_at": {"gte": since}}}
        print(f"(only runs since {since})")
    client = OpenSearchClient()
    try:
        result = await client.search(settings.opensearch_investigation_index, {
            "size": limit,
            "query": query,
            "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}],
        })
        runs = [h["_source"] for h in result.get("hits", {}).get("hits", [])]
        if not runs:
            print("No investigations stored yet.")
            return

        print(f"{'=' * 78}\nAUDIT OF {len(runs)} STORED INVESTIGATION(S)\n{'=' * 78}")

        categories = Counter()
        analysts = Counter()
        codes = Counter()
        planners = Counter()
        disagreements = 0
        confidences = []
        no_incident = 0

        for run in runs:
            analysis = run.get("analysis", {})
            categories[analysis.get("category", "?")] += 1
            analysts[analysis.get("analyst", "?")] += 1
            planners[run.get("plan", {}).get("planner", "?")] += 1
            confidences.append(float(analysis.get("confidence") or 0))
            if analysis.get("agrees_with_engine") is False:
                disagreements += 1
            if not analysis.get("incident_detected"):
                no_incident += 1
            for issue in analysis.get("verification", []):
                codes[issue.get("code", "?")] += 1

        print("\nCause categories chosen:")
        for name, count in categories.most_common():
            print(f"  {count:>4}  {name}")

        print(f"\nAnalyst: {dict(analysts)}      Planner: {dict(planners)}")
        print(f"Model disagreed with the rule engine: {disagreements}/{len(runs)}")
        print(f"No incident detected:                 {no_incident}/{len(runs)}")
        if confidences:
            print(f"Confidence: mean {sum(confidences) / len(confidences):.2f}  "
                  f"min {min(confidences):.2f}  max {max(confidences):.2f}")

        print("\nVerification issues, by frequency:")
        for code, count in codes.most_common():
            share = count / len(runs)
            flag = "  <-- fires on most runs; likely a pipeline bug" if share > 0.6 else ""
            print(f"  {count:>4} ({share:>4.0%})  {code}{flag}")

        print("\nMost recent runs:")
        for run in runs[:12]:
            analysis = run.get("analysis", {})
            timings = run.get("timings_ms", {})
            total = sum(float(v) for v in timings.values()) / 1000 if timings else 0
            print(f"  {run.get('created_at', '?')[:19]}  "
                  f"{analysis.get('category', '?'):<24} "
                  f"conf={float(analysis.get('confidence') or 0):.2f} "
                  f"{analysis.get('analyst', '?'):<14} {total:>5.0f}s  "
                  f"{(run.get('question') or '')[:44]}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
