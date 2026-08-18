"""
Reports how much of the model Ollama actually placed on the GPU.

Context length is the hidden cost here. The weights are a fixed size, but the KV
cache grows linearly with `num_ctx`, and on a card with limited VRAM the two
together decide whether the model runs on the GPU or gets pushed onto the CPU —
where it still works, just several times slower.

    python -m eval.gpu_check            # try a range of context sizes
    python -m eval.gpu_check 16384      # test one
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from app.config import settings

SIZES = [2048, 4096, 8192, 16384]


async def measure(client: httpx.AsyncClient, num_ctx: int) -> dict | None:
    # Unload first so each measurement reflects this context size alone.
    await client.post("/api/generate", json={
        "model": settings.ollama_model, "prompt": "hi",
        "keep_alive": 0, "stream": False,
    })
    await asyncio.sleep(1)

    await client.post("/api/generate", json={
        "model": settings.ollama_model, "prompt": "hi", "stream": False,
        "options": {"num_ctx": num_ctx},
    })
    response = await client.get("/api/ps")
    for model in response.json().get("models", []):
        total = model.get("size", 0)
        vram = model.get("size_vram", 0)
        return {
            "num_ctx": num_ctx,
            "total_gb": total / 1e9,
            "vram_gb": vram / 1e9,
            "cpu_gb": (total - vram) / 1e9,
            "gpu_percent": (100 * vram / total) if total else 0.0,
        }
    return None


async def main() -> int:
    sizes = [int(sys.argv[1])] if len(sys.argv) > 1 else SIZES
    print(f"model: {settings.ollama_model}   host: {settings.ollama_base_url}")
    print(f"agent is configured with num_ctx={settings.ollama_num_ctx}\n")
    print(f"{'num_ctx':>8} {'total':>9} {'on GPU':>9} {'on CPU':>9} {'GPU share':>10}")
    print("-" * 50)

    results = []
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=600.0) as client:
        for size in sizes:
            try:
                result = await measure(client, size)
            except httpx.HTTPError as exc:
                print(f"{size:>8}  unreachable: {exc}")
                continue
            if not result:
                print(f"{size:>8}  model did not stay loaded")
                continue
            results.append(result)
            print(f"{result['num_ctx']:>8} {result['total_gb']:>8.2f}G "
                  f"{result['vram_gb']:>8.2f}G {result['cpu_gb']:>8.2f}G "
                  f"{result['gpu_percent']:>9.0f}%")

    fully = [r for r in results if r["gpu_percent"] >= 99]
    if fully:
        best = max(fully, key=lambda r: r["num_ctx"])
        print(f"\nLargest context that fits entirely on the GPU: {best['num_ctx']}")
        if best["num_ctx"] < settings.ollama_num_ctx:
            print(f"The agent is set to {settings.ollama_num_ctx}, which spills onto the "
                  f"CPU. Lowering OLLAMA_NUM_CTX to {best['num_ctx']} would keep it on "
                  f"the GPU — but check the truncation warnings first: a prompt that no "
                  f"longer fits is a worse problem than a slow one.")
    elif results:
        print("\nNo tested context size fits entirely on the GPU. The weights alone may "
              "exceed available VRAM; a smaller quantisation of the same model would "
              "help more than reducing context.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
