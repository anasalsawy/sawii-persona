"""A fake OpenAI-compatible provider (SSE streaming + tool-call deltas + embeddings)
used by the live smoke test to exercise the real HTTP client code path."""
import json
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from fakes import make_a, make_b, make_c  # noqa: E402

BRAINS = {"model-a": make_a(use_tools=True), "model-b": make_b(), "model-c": make_c(),
          "model-consult": None}


async def chat(request):
    body = await request.json()
    model = body["model"]
    if model == "model-consult":
        text, calls = "- Friday exams: sleep matters more than cramming.", []
    else:
        res = await BRAINS[model].chat(body["messages"], tools=body.get("tools"),
                                       json_mode="response_format" in body)
        text, calls = res.text, res.tool_calls
    if not body.get("stream"):
        msg = {"role": "assistant", "content": text}
        if calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments}} for c in calls]
        return JSONResponse({"choices": [{"index": 0, "message": msg,
                                          "finish_reason": "tool_calls" if calls else "stop"}]})

    async def gen():
        for i in range(0, len(text), 7):
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text[i:i + 7]}}]}) + "\n\n"
        for n, c in enumerate(calls):
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": n, "id": c.id, "type": "function", "function": {"name": c.name, "arguments": ""}}]}}]}) + "\n\n"
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": n, "function": {"arguments": c.arguments}}]}}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {},
                                                  "finish_reason": "tool_calls" if calls else "stop"}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


async def embeddings(request):
    from sawii_persona.embedder import hash_embed
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return JSONResponse({"data": [{"index": i, "embedding": hash_embed("sem:" + t)} for i, t in enumerate(inputs)]})


app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                        Route("/v1/embeddings", embeddings, methods=["POST"])])

if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/src")
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
