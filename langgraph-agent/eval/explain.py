"""
Explains a stored investigation: the windows it chose, every signal it fired,
how the rules ranked the candidates, and where the model landed.

    python -m eval.explain            # the most recent run
    python -m eval.explain inv-abc123 # a specific one
"""
from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.sources.opensearch import OpenSearchClient


async def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    client = OpenSearchClient()
    try:
        if wanted:
            body = {"size": 1, "query": {"term": {"id": wanted}}}
        else:
            body = {"size": 1, "query": {"match_all": {}},
                    "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}]}
        result = await client.search(settings.opensearch_investigation_index, body)
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            print("No matching investigation.")
            return
        run = hits[0]["_source"]
    finally:
        await client.close()

    analysis = run.get("analysis", {})
    windows = run.get("windows", {})
    plan = run.get("plan", {})

    print(f"{'=' * 78}\n{run.get('id')}   {run.get('created_at', '')[:19]}\n{'=' * 78}")
    print(f"question : {run.get('question')}")
    print(f"system   : {plan.get('system_id')}/{plan.get('environment')}  "
          f"focus={plan.get('service')}  planner={plan.get('planner')}")

    print("\nWINDOWS")
    incident = windows.get("incident") or {}
    baseline = windows.get("baseline") or {}
    print(f"  incident : {incident.get('start', '?')[:19]} -> {incident.get('end', '?')[:19]}")
    print(f"  baseline : {baseline.get('start', 'none')[:19] if baseline else 'NONE'}"
          f" -> {baseline.get('end', '')[:19] if baseline else ''}")
    print(f"  onset    : {windows.get('onset')}   detected={windows.get('onset_detected')}")
    print(f"  method   : {windows.get('method')}")

    print(f"\nSIGNALS ({len(run.get('signals', []))})")
    for signal in run.get("signals", []):
        magnitude = signal.get("magnitude") or {}
        scale = ""
        if magnitude.get("incident") is not None:
            scale = f"  {magnitude['incident']:.4g} {magnitude.get('unit', '')}"
            if magnitude.get("baseline") is not None:
                scale += f" vs {magnitude['baseline']:.4g} baseline"
            if magnitude.get("ratio") is not None:
                scale += f" ({magnitude['ratio']:.1f}x)"
        print(f"  {signal.get('severity', ''):<9} {signal.get('type', ''):<24} "
              f"svc={str(signal.get('service')):<14} "
              f"onset={str(signal.get('first_seen'))[11:19]}{scale}")

    print(f"\nCANDIDATES (rule ranking)")
    chosen = analysis.get("chosen_candidate_id")
    engine_top = analysis.get("engine_top_candidate_id")
    for candidate in run.get("candidates", []):
        marks = []
        if candidate["id"] == chosen:
            marks.append("MODEL CHOSE")
        if candidate["id"] == engine_top:
            marks.append("RULES RANKED #1")
        suffix = ("   <-- " + ", ".join(marks)) if marks else ""
        print(f"  {candidate['id']} score={candidate['score']:.2f} "
              f"{candidate['category']:<26} svc={candidate.get('service')}{suffix}")
        print(f"      {candidate['hypothesis']}")
        print(f"      why: {candidate.get('rationale', '')[:200]}")
        if candidate.get("contradicting_signals"):
            print(f"      against: {candidate['contradicting_signals']}")

    print("\nVERDICT")
    print(f"  category   : {analysis.get('category')}   confidence={analysis.get('confidence')}")
    print(f"  analyst    : {analysis.get('analyst')}   agrees_with_engine="
          f"{analysis.get('agrees_with_engine')}")
    print(f"  summary    : {analysis.get('cause_summary', '')[:400]}")
    print("  verification:")
    for issue in analysis.get("verification", []):
        print(f"    [{issue.get('severity')}] {issue.get('code')}: {issue.get('detail')[:180]}")


if __name__ == "__main__":
    asyncio.run(main())
