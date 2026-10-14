"""The live LLM path (Nebius Token Factory / Nemotron) against a fake OpenAI-compatible server."""

import asyncio
import json
import random

import httpx

from flashcrash.brain import LLMBrain
from flashcrash.personas import spawn_agents
from flashcrash.sim import Simulation


def fake_nebius(log):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            ids = ["nvidia/Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3-Super-120B-A12B", "meta-llama/Llama-3.3-70B"]
            return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model", "owned_by": "x"} for i in ids]})
        body = json.loads(request.content)
        log.append(body)
        if "chat_template_kwargs" in body and log.count("rejected") == 0:
            log.append("rejected")
            return httpx.Response(400, json={"error": {"message": "unknown field chat_template_kwargs"}})
        system = body["messages"][0]["content"]
        if "JSON object" in system:  # a trader turn
            content = '<think>news is good</think>{"action":"buy","qty":7,"price":null,"thought":"Nemotron says moon"}'
        elif "chaos editor" in system:
            content = '"Doge Burger CEO elected mayor of the Moon"\nextra line'
        else:
            content = "The AI SEC has spoken."
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })
    return handler


def test_nebius_path_end_to_end(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "test")
    for k in ("FAST_MODEL", "BIG_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    log = []

    async def run():
        brain = LLMBrain("nebius", 4, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_nebius(log))))
        await brain.resolve_models()
        sim = Simulation(brain, n_agents=30, workers=4, seed=1)
        agent = next(a for a in sim.world.agents.values() if a.archetype.key == "wsb")
        d = await brain.decide(agent, sim.context_for(agent, "MEME"))
        headline = await brain.write_headline({"ticker": "MEME", "company": "Doge Burger", "blurb": "meme stock",
                                               "price": 8, "change_pct": 0, "news": []})
        said = await brain.narrate("sys", "user", "fallback")
        return brain, d, headline, said

    brain, d, headline, said = asyncio.run(run())
    assert brain.fast_model == "nvidia/Nemotron-3-Nano-30B-A3B" and brain.big_model == "nvidia/Nemotron-3-Super-120B-A12B"
    assert (d.action, d.qty, d.price, d.thought) == ("buy", 7, None, "Nemotron says moon")
    assert d.model == brain.fast_model
    assert headline == "Doge Burger CEO elected mayor of the Moon"
    assert said == "The AI SEC has spoken."
    assert brain.stats.calls == 3 and brain.stats.tokens_per_s if hasattr(brain.stats, "tokens_per_s") else True
    calls = [b for b in log if isinstance(b, dict)]
    assert "rejected" in log and "chat_template_kwargs" not in calls[-1]  # retried without the Nemotron-only knob
    prompt = calls[1]["messages"][0]["content"] + calls[1]["messages"][1]["content"]
    assert "$MEME" in prompt and "posting style" in prompt


def test_narrate_falls_back_when_provider_is_down(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "test")

    async def run():
        down = httpx.MockTransport(lambda r: httpx.Response(503, json={"error": {"message": "overloaded"}}))
        brain = LLMBrain("nebius", 2, http_client=httpx.AsyncClient(transport=down))
        return brain, await brain.narrate("s", "u", "template text")

    brain, said = asyncio.run(run())
    assert said == "template text" and brain.stats.errors >= 1 and "503" in brain.stats.last_error
