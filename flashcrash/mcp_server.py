"""MCP server (Streamable HTTP) so Alexa+ or any agent can watch and move the market by voice.

"Alexa, ask Flash Crash how the market is doing."
"Alexa, tell Flash Crash the CEO just got arrested."
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .personas import COMPANY, TICKER
from .sim import Simulation


def build_mcp(sim: Simulation) -> MCPServer:
    mcp = MCPServer(
        name="flash-crash",
        title="Flash Crash",
        description=f"A live stock market for ${TICKER} run by hundreds of AI traders.",
        instructions=(
            f"Flash Crash is a living-room game: a fake stock ${TICKER} ({COMPANY}) traded by hundreds of AI agents "
            "with personalities (degens, value investors, panic sellers, whales). Players move the market by leaking "
            "breaking news and by trading. Keep spoken answers short and dramatic."
        ),
        version="0.1.0",
    )

    @mcp.tool(title="Market status")
    def market_status() -> dict:
        """Current price, move since open, which trader types are winning, and the latest headline."""
        s = sim.snapshot()
        news = sim.world.news[-1].text if sim.world.news else None
        top = s["leaderboard"][:3]
        return {
            "ticker": TICKER,
            "price": s["price"],
            "change_pct": s["change_pct"],
            "ai_traders": s["agents"],
            "winning": [f"{r['label']} {r['pnl_pct']:+.1f}%" for r in top],
            "losing": [f"{r['label']} {r['pnl_pct']:+.1f}%" for r in s["leaderboard"][-2:]],
            "latest_headline": news,
            "summary": f"${TICKER} is at ${s['price']:.2f}, {s['change_pct']:+.1f}% today. "
                       f"{top[0]['label']}s are winning." if top else "",
        }

    @mcp.tool(title="Leak breaking news")
    async def leak_news(headline: str, author: str = "Alexa") -> dict:
        """Inject a breaking-news headline into the market. Every AI trader will read it and react.
        Example: 'GPU CEO arrested at the airport' or 'GPU wins giant contract with NASA'."""
        price_before = sim.price
        item = await sim.add_news(headline, "alexa", author)
        return {"ok": True, "headline": item.text, "price_at_leak": round(price_before, 2),
                "message": "Headline is live. Watch the traders react on the TV."}

    @mcp.tool(title="Chaos headline")
    async def chaos() -> dict:
        """Ask the AI newsroom to invent a surprise market-moving headline."""
        item = await sim.newsroom()
        return {"ok": True, "headline": item.text}

    @mcp.tool(title="Trade")
    async def trade(player: str, side: str, shares: int) -> dict:
        """Buy or sell shares for a human player (joins them automatically). side is 'buy' or 'sell'."""
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "side must be buy or sell"}
        p = next((a for a in sim.world.agents.values() if a.human and a.name.lower() == player.lower()), None)
        p = p or sim.join(player)
        item = await sim.human_trade(p.id, side, max(1, int(shares)))
        return {"ok": True, "filled": item.filled, "price": round(sim.price, 2), "portfolio": sim.portfolio(p.id)}

    @mcp.tool(title="Control game")
    async def control(action: str) -> dict:
        """Control the game: 'pause', 'resume', 'reset' (new round), 'autopilot_on' or 'autopilot_off' (AI news every ~40s)."""
        if action == "pause":
            sim.paused = True
        elif action == "resume":
            sim.paused = False
        elif action == "reset":
            sim.reset()
        elif action in ("autopilot_on", "autopilot_off"):
            sim.autopilot = action == "autopilot_on"
        else:
            return {"ok": False, "error": "unknown action"}
        return {"ok": True, "action": action}

    return mcp
