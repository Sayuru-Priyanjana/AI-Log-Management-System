"""
Runs one investigation against the live agent and prints the reasoning trace.

    python -m eval.trace "why is checkout failing?"
    python -m eval.trace "show me the errors from payment-api"
    python -m eval.trace "how many errors per service?"

Prints every thought, tool call and observation as it happens, then the verified
answer with its citations and the factors behind its confidence. This is the
quickest way to see whether the loop is reasoning or flailing.
"""
from __future__ import annotations

import asyncio
import sys

import httpx

BASE = "http://localhost:8000"


def wrap(text: str, indent: str = "           ", width: int = 100) -> str:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return f"\n{indent}".join(lines)


async def main() -> int:
    question = " ".join(sys.argv[1:]) or "why is checkout failing?"
    payload = {"system_id": "shopdemo", "environment": "staging", "question": question}

    print(f"\n\033[1mQ: {question}\033[0m\n" + "=" * 110)

    async with httpx.AsyncClient(timeout=420.0) as client:
        async with client.stream("POST", f"{BASE}/api/investigations", json=payload) as response:
            if response.status_code != 200:
                print(f"HTTP {response.status_code}: {await response.aread()}")
                return 1
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                import json
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                render(event)
    return 0


def render(event: dict) -> None:
    stage, data = event.get("stage"), event.get("data", {})

    if stage == "plan":
        print(f"PLAN       intent={data.get('intent')} mode={data.get('answer_mode')} "
              f"service={data.get('service')} planner={data.get('planner')}")
        for note in data.get("notes", []):
            print(f"           note: {note}")

    elif stage == "windows":
        print(f"WINDOW     incident {str(data.get('incident', {}).get('start'))[11:19]}"
              f"-{str(data.get('incident', {}).get('end'))[11:19]}  "
              f"onset={str(data.get('onset'))[11:19]}  detected={data.get('onset_detected')}")

    elif stage == "evidence":
        logs = data.get("logs", {})
        print(f"EVIDENCE   {logs.get('documents')} logs ({logs.get('patterns')} patterns), "
              f"{data.get('events', {}).get('count')} events, "
              f"{data.get('metrics', {}).get('series')} metric series")
        if data.get("gaps"):
            print(f"           gaps: {data['gaps']}")

    elif stage == "signals":
        print(f"SIGNALS    {data.get('count')} detected")
        for item in data.get("signals", [])[:8]:
            magnitude = (item.get("magnitude") or {})
            scale = ""
            if magnitude.get("incident") is not None:
                scale = f" — {magnitude['incident']:.4g} {magnitude.get('unit', '')}"
            print(f"           {item['severity']:<8} {item['type']:<24} "
                  f"{item.get('service') or '-'}{scale}")

    elif stage == "candidates":
        print(f"CANDIDATES {len(data.get('candidates', []))}")
        for item in data.get("candidates", [])[:3]:
            print(f"           {item['score']:.2f} {item['category']:<24} "
                  f"{item.get('service') or '-'}")

    elif stage == "reasoning":
        kind = data.get("type")
        if kind == "thought":
            print(f"\n\033[36mTHOUGHT {data.get('step')}\033[0m  {wrap(data.get('text', ''))}")
        elif kind == "action":
            print(f"\033[33mACTION\033[0m     {data.get('tool')}({data.get('input')})")
        elif kind == "observation":
            text = data.get("text", "")
            head = text.split("\n")[0]
            extra = len(text.split("\n")) - 1
            print(f"\033[32mOBSERVED\033[0m   {wrap(head)}"
                  + (f"\n           ... {extra} more line(s), "
                     f"{len(data.get('evidence_ids') or [])} evidence id(s)"
                     if extra else ""))
        elif kind in ("note", "exhausted", "error"):
            print(f"\033[31m{kind.upper()}\033[0m       {data.get('message')}")

    elif stage == "evidence_timeline":
        entries = data.get("entries", [])
        folded = data.get("collapsed_from") or 0
        print("\n" + "=" * 110)
        print(f"\033[1mEVIDENCE TIMELINE\033[0m  {len(entries)} distinct entries"
              + (f", folded from {folded:,} log documents" if folded else ""))
        print(f"  window {str(data.get('window', {}).get('start'))[11:19]} – "
              f"{str(data.get('window', {}).get('end'))[11:19]}\n")
        for entry in entries:
            mark = "\033[33m*\033[0m" if entry.get("notable") else " "
            occurrences = (f"  \033[36m×{entry['occurrences']:,}\033[0m"
                           if entry.get("occurrences", 1) > 1 else "")
            baseline = ""
            if entry.get("baseline_occurrences") is not None:
                baseline = (" [new]" if entry["baseline_occurrences"] == 0
                            else f" [baseline ×{entry['baseline_occurrences']:,}]")
            span = ""
            if entry.get("occurrences", 1) > 1 and entry.get("last_seen"):
                span = f"  {str(entry['first_seen'])[11:19]}→{str(entry['last_seen'])[11:19]}"
            print(f"{mark} {str(entry['first_seen'])[11:19]} "
                  f"{entry['kind']:<7} {(entry.get('level') or ''):<9}"
                  f"{entry['title'][:70]}{occurrences}{baseline}{span}")
            if entry.get("notable") and entry.get("notable_reason"):
                print(f"      \033[33m{entry['notable_reason']}\033[0m")

    elif stage == "answer":
        print("\n" + "=" * 110)
        print(f"\033[1mANSWER\033[0m     mode={data.get('mode')}  "
              f"confidence={data.get('confidence'):.2f}")
        print(f"\n  {data.get('headline')}\n")
        if data.get("detail"):
            print(f"  {wrap(data['detail'], '  ')}\n")

        if data.get("reasoning"):
            print("  Reasoning:")
            for step in data["reasoning"]:
                ids = ", ".join(step.get("evidence_ids") or []) or "NO EVIDENCE CITED"
                print(f"    - [{step.get('kind')}] {step['claim']}")
                if step.get("because"):
                    print(f"      because {step['because']}")
                print(f"      evidence: {ids}")

        if data.get("assumptions"):
            print("\n  Assumptions:")
            for item in data["assumptions"]:
                print(f"    - {item['statement']}")
                if item.get("impact_if_wrong"):
                    print(f"      if wrong: {item['impact_if_wrong']}")

        if data.get("table"):
            table = data["table"]
            print(f"\n  Data ({table.get('total_matched')} matched): "
                  f"{table.get('query_description')}")
            print("    " + " | ".join(table.get("columns", [])))
            for row in table.get("rows", [])[:10]:
                print("    " + " | ".join(str(c)[:60] for c in row))

        print("\n  Confidence factors:")
        for factor in data.get("confidence_factors", []):
            arrow = "+" if factor["direction"] == "raises" else "-"
            print(f"    {arrow} {factor['factor']}")

        citations = data.get("citations", [])
        bad = [c for c in citations if c["status"] == "unresolved"]
        print(f"\n  Citations: {len(citations)} "
              f"({len(bad)} unresolved{': ' + ', '.join(c['id'] for c in bad) if bad else ''})")

        if data.get("limitations"):
            print("\n  Limitations:")
            for item in data["limitations"]:
                print(f"    - {item}")
        if data.get("next_steps"):
            print("\n  Next steps:")
            for item in data["next_steps"]:
                print(f"    - {item}")

    elif stage == "error":
        print(f"\033[31mERROR\033[0m      {data}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
