"""Chaos-mode provider for failure-mode hardening tests.

Acts like fake_provider.py but can be configured to:
  - Always return HTTP 500 (server_error)
  - Always return HTTP 429 (rate_limited)
  - Always return malformed JSON
  - Sleep N seconds before responding (slow_provider)
  - Randomly fail with one of the above per-request

Usage:
    PYTHONPATH=src python -m arch_finetune.chaos_provider 8802 --mode server_error
    PYTHONPATH=src python -m arch_finetune.chaos_provider 8803 --mode slow --delay 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route


async def chat(request):
    body = await request.json()
    model = body.get("model", "model-a")
    if request.app.state.mode == "server_error":
        return PlainTextResponse("upstream provider down", status_code=500)
    if request.app.state.mode == "rate_limited":
        return JSONResponse({"error": {"message": "rate limited"}}, status_code=429)
    if request.app.state.mode == "malformed_json":
        return PlainTextResponse("not even json {", status_code=200)
    if request.app.state.mode == "slow":
        delay = request.app.state.delay
        await asyncio.sleep(delay)
    if request.app.state.mode == "random":
        import random
        choice = random.choice(["ok", "server_error", "malformed_json"])
        if choice == "ok":
            pass
        elif choice == "server_error":
            return PlainTextResponse("upstream down", status_code=500)
        elif choice == "malformed_json":
            return PlainTextResponse("not even json {", status_code=200)
    # OK path: return a minimal valid response.
    return JSONResponse({
        "choices": [{
            "index": 0,
            "message": {"role": "assistant",
                        "content": json.dumps({"note": f"chaos-ok {model}"})},
            "finish_reason": "stop",
        }],
    })


async def embeddings(request):
    if request.app.state.mode == "server_error":
        return PlainTextResponse("upstream down", status_code=500)
    return JSONResponse({"data": [{"index": 0, "embedding": [0.0] * 4}]})


app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                        Route("/v1/embeddings", embeddings, methods=["POST"])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--mode", choices=["ok", "server_error", "rate_limited",
                                       "malformed_json", "slow", "random"],
                    default="ok")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()
    app.state.mode = args.mode
    app.state.delay = args.delay
    print(f"[chaos_provider] mode={args.mode} delay={args.delay}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()