"""MCP server (Streamable HTTP) so Alexa+ or any agent can watch and move the market by voice.

"Alexa, ask Flash Crash how the market is doing."
"Alexa, tell Flash Crash the CEO just got arrested."
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .personas import STOCKS
from .sim import Simulation

LISTING = ", ".join(f"${s.ticker} ({s.company}, {s.blurb})" for s in STOCKS)


def build_mcp(sim: Simulation) -> MCPServer:
    mcp = MCPServer(
        name="flash-crash",
        title="Flash Crash",
        description="A live stock market run by hundreds of AI traders.",
        instructions=(
            f"Flash Crash is a living-room game: fake stocks {LISTING} traded by hundreds of AI agents "
            "with personalities (degens, value investors, panic sellers, whales). Players move the market by leaking "
            "breaking news and by trading. Keep spoken answers short and dramatic."
        ),
        version="0.2.0",
    )

    @mcp.tool(title="Market status")
    def market_status() -> dict:
        """Prices and moves for every stock, which trader types are winning, the round clock and the latest headline."""
        s = sim.snapshot()
        news = sim.world.news[-1].text if sim.world.news else None
        top = s["leaderboard"][:3]
        mk = s["markets"]
        best = max(mk, key=lambda m: m["change_pct"])
        worst = min(mk, key=lambda m: m["change_pct"])
        return {
            "stocks": [{"ticker": m["ticker"], "price": m["price"], "change_pct": m["change_pct"]} for m in mk],
            "ai_traders": s["agents"],
            "winning": [f"{r['label']} {r['pnl_pct']:+.1f}%" for r in top],
            "players": [f"{p['name']} {p['pnl_pct']:+.1f}%" for p in s["players"][:5]],
            "round_seconds_left": s["round_left"],
            "latest_headline": news,
            "sec_fines": [c["text"] for c in sim.world.sec_cases[-2:]],
            "summary": f"${best['ticker']} is the top mover at {best['change_pct']:+.1f}%, "
                       f"${worst['ticker']} the worst at {worst['change_pct']:+.1f}%. "
                       + (f"{top[0]['label']}s are winning." if top else ""),
        }

    @mcp.tool(title="Leak breaking news")
    async def leak_news(headline: str, author: str = "Alexa", ticker: str | None = None) -> dict:
        """Inject a breaking-news headline. Every AI trader reads it and reacts. Name a company in the
        headline (GPU, RoboRide, Nova Fusion, Doge Burger) or pass ticker; otherwise it's market-wide news.
        Example: 'GPU CEO arrested at the airport' or 'Doge Burger adds a golden burger'."""
        item = await sim.add_news(headline, "alexa", author, ticker)
        return {"ok": True, "headline": item.text, "stocks": item.tickers, "prices_at_leak": item.prices,
                "message": "Headline is live. Watch the traders react on the TV."}

    @mcp.tool(title="Why did it move?")
    async def explain_move(ticker: str = "GPU") -> dict:
        """Market forensics: explain why a stock moved in the last 30 seconds: the headline that triggered it and
        which AI trader types (degens, whales, panic sellers...) drove the buying or selling. ticker: GPU, ROBO, NOVA or MEME."""
        d = await sim.explain(ticker, 30.0)
        return {"ticker": d["ticker"], "move_pct": d["move_pct"], "headline": d["headline"],
                "drivers": [f"{x['label']} {'bought' if x['net_usd'] > 0 else 'sold'} ${abs(x['net_usd']):,.0f}" for x in d["drivers"][:3]],
                "summary": d["summary"]}

    @mcp.tool(title="Chaos headline")
    async def chaos(ticker: str | None = None) -> dict:
        """Ask the AI newsroom to invent a surprise market-moving headline (optionally about one ticker)."""
        item = await sim.newsroom(ticker)
        return {"ok": True, "headline": item.text, "stocks": item.tickers}

    @mcp.tool(title="Trade")
    async def trade(player: str, side: str, shares: int, ticker: str = "GPU") -> dict:
        """Buy or sell shares of a stock for a human player (joins them automatically).
        side is 'buy' or 'sell'; ticker is one of GPU, ROBO, NOVA, MEME."""
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "side must be buy or sell"}
        p = next((a for a in sim.world.agents.values() if a.human and a.name.lower() == player.lower()), None)
        p = p or sim.join(player)
        try:
            item = await sim.human_trade(p.id, side, max(1, int(shares)), ticker)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "ticker": item.ticker, "filled": item.filled, "price": item.fill_price,
                "portfolio": sim.portfolio(p.id)}

    @mcp.tool(title="Control game")
    async def control(action: str, minutes: float = 5) -> dict:
        """Control the game: 'start_round' (timed round, default 5 minutes, fresh bankrolls), 'demo' (3-minute scripted
        show), 'pause', 'resume', 'reset' (new free-play market), 'autopilot_on' or 'autopilot_off' (AI news every ~40s)."""
        if action == "pause":
            sim.paused = True
        elif action == "resume":
            sim.paused = False
            if sim.round_over:
                sim.round_minutes = 0
        elif action == "start_round":
            await sim.start_round(min(30.0, max(1.0, minutes)))
        elif action == "demo":
            await sim.demo()
        elif action == "reset":
            sim.reset()
        elif action in ("autopilot_on", "autopilot_off"):
            sim.autopilot = action == "autopilot_on"
        else:
            return {"ok": False, "error": "unknown action"}
        return {"ok": True, "action": action}

    return mcp
