"""Real-world headlines via Tavily (optional)."""

from __future__ import annotations

import os

import httpx


async def tavily_headlines(query: str, limit: int = 3) -> list[str]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "topic": "news", "max_results": limit, "days": 3},
        )
        r.raise_for_status()
        return [x["title"].strip() for x in r.json().get("results", []) if x.get("title")][:limit]
