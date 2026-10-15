"""FastAPI app: TV dashboard, phone controller, REST + WebSocket, and the MCP endpoint at /mcp."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .brain import make_brain  # noqa: E402
from .mcp_server import build_mcp  # noqa: E402
from .market_days import MarketDays, MarketDaysAuthError, MarketDaysError, MarketDaysInputError  # noqa: E402
from .news import tavily_headlines  # noqa: E402
from .sim import Simulation  # noqa: E402

STATIC = Path(__file__).parent / "static"

brain = make_brain(int(os.getenv("CONCURRENCY", "24")))
sim = Simulation(
    brain,
    n_agents=int(os.getenv("AGENTS", "300")),
    workers=int(os.getenv("CONCURRENCY", "24")),
    autopilot_news_min=float(os.getenv("AUTOPILOT_NEWS_MIN_SECONDS", "45")),
    autopilot_news_max=float(os.getenv("AUTOPILOT_NEWS_MAX_SECONDS", "75")),
)
mcp = build_mcp(sim)
market_days = MarketDays.from_env(brain)


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _mcp_security() -> TransportSecuritySettings:
    """DNS-rebinding protection: only accept MCP calls addressed to this machine or PUBLIC_URL."""
    from urllib.parse import urlparse

    hosts = ["localhost", "127.0.0.1", lan_ip()]
    if os.getenv("PUBLIC_URL"):
        hosts.append(urlparse(os.environ["PUBLIC_URL"]).hostname)
    hosts += [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    allowed = [h for host in hosts for h in (host, f"{host}:*")]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed,
                                     allowed_origins=[f"{scheme}://{h}" for scheme in ("http", "https") for h in allowed])


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send_all(self, msg: dict) -> None:
        data = json.dumps(msg, default=str)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()


async def _push_snapshots():
    while True:
        await asyncio.sleep(0.5)
        if hub.clients:
            await hub.send_all({"type": "snapshot", "data": sim.snapshot()})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await brain.resolve_models()
    async def days_push(game_id, event):  # live updates for /days on the shared WebSocket
        await hub.send_all({"type": "market_days", "game_id": game_id, "event": event})

    market_days.listeners.append(days_push)
    try:
        await market_days.start()
    except MarketDaysError as exc:  # a bad DATABASE_URL must never take the party game down
        print(f"Market Days disabled: {exc}")
        market_days.enabled = False
    sim.listeners.append(hub.send_all)
    sim.start()
    pusher = asyncio.create_task(_push_snapshots())
    async with mcp.session_manager.run():
        yield
    pusher.cancel()
    await market_days.stop()
    await sim.stop()


app = FastAPI(title="Flash Crash", lifespan=lifespan)
days_origins = [origin.strip() for origin in os.getenv("MARKET_DAYS_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
if days_origins:
    app.add_middleware(CORSMiddleware, allow_origins=days_origins, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def root():
    return RedirectResponse("/tv")


@app.get("/tv")
def tv():
    return FileResponse(STATIC / "tv.html")


@app.get("/play")
def play():
    return FileResponse(STATIC / "play.html")


@app.get("/days")
def days():
    return FileResponse(STATIC / "market_days.html")


@app.get("/api/info")
def info():
    port = int(os.getenv("PORT", "8000"))
    public = os.getenv("PUBLIC_URL") or f"http://{lan_ip()}:{port}"
    return {"join_url": f"{public}/play", "mcp_url": f"{public}/mcp", "tavily": bool(os.getenv("TAVILY_API_KEY"))}


@app.get("/api/state")
def state():
    return {**sim.snapshot(), **sim.history()}


class NewsIn(BaseModel):
    text: str
    source: str = "crowd"
    author: str = "anon"
    ticker: str | None = None  # the stock the leaker was looking at
    player_id: str | None = None  # lets the AI SEC check the leaker's trades


class NewsIdeaIn(BaseModel):
    idea: str
    ticker: str | None = None


@app.post("/api/news/preview")
async def preview_news(body: NewsIdeaIn):
    idea = " ".join(body.idea.split())[:160]
    if not idea:
        raise HTTPException(400, "empty news idea")
    return await sim.preview_news_idea(idea, body.ticker)


class DaysGameIn(BaseModel):
    name: str = "Market Day"
    symbols: list[str] = ["SPY", "AAPL", "NVDA"]


class DaysCycleIn(BaseModel):
    symbol: str | None = None


class DaysJoinIn(BaseModel):
    name: str


class DaysTradeIn(BaseModel):
    player_id: str
    token: str = ""  # returned once by /join; proves you own player_id
    symbol: str
    side: str
    qty: int


def _days_error(exc: Exception):
    if isinstance(exc, MarketDaysAuthError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, MarketDaysInputError):
        raise HTTPException(400, str(exc))
    raise HTTPException(503, str(exc) if isinstance(exc, MarketDaysError) else "Market Days request failed")


def _days_operator(token: str | None) -> None:
    """Creating games and running AI cycles place orders on the Alpaca paper account, so on a public deployment they
    need MARKET_DAYS_ADMIN_TOKEN (sent as X-Admin-Token). Without Alpaca keys (simulated quotes) they stay open."""
    want = os.getenv("MARKET_DAYS_ADMIN_TOKEN", "")
    if want:
        if not secrets.compare_digest(token or "", want):
            raise HTTPException(401, "operator token required (X-Admin-Token)")
    elif market_days.alpaca is not None:
        raise HTTPException(403, "set MARKET_DAYS_ADMIN_TOKEN to allow AI paper orders through the API")


@app.get("/api/days/config")
async def days_config():
    return {**market_days.config(), "operator_token_required": bool(os.getenv("MARKET_DAYS_ADMIN_TOKEN"))
            or market_days.alpaca is not None}


@app.get("/api/days/games")
async def days_games():
    try:
        return await market_days.games()
    except MarketDaysError as exc:
        _days_error(exc)


@app.post("/api/days/games")
async def create_days_game(body: DaysGameIn, x_admin_token: str | None = Header(None)):
    _days_operator(x_admin_token)
    try:
        return await market_days.create_game(body.name, body.symbols)
    except MarketDaysError as exc:
        _days_error(exc)


@app.get("/api/days/games/{game_id}")
async def days_game(game_id: str):
    try:
        return await market_days.state(game_id)
    except MarketDaysError as exc:
        _days_error(exc)


@app.post("/api/days/games/{game_id}/join")
async def days_join(game_id: str, body: DaysJoinIn):
    try:
        return await market_days.join(game_id, body.name)
    except MarketDaysError as exc:
        _days_error(exc)


@app.post("/api/days/games/{game_id}/trade")
async def days_trade(game_id: str, body: DaysTradeIn):
    try:
        return await market_days.trade(game_id, body.player_id, body.symbol, body.side, body.qty, body.token)
    except MarketDaysError as exc:
        _days_error(exc)


@app.post("/api/days/games/{game_id}/ai-cycle")
async def days_ai_cycle(game_id: str, body: DaysCycleIn, x_admin_token: str | None = Header(None)):
    _days_operator(x_admin_token)
    try:
        return await market_days.ai_cycle(game_id, body.symbol)
    except MarketDaysError as exc:
        _days_error(exc)


@app.post("/api/news")
async def post_news(body: NewsIn):
    if not body.text.strip():
        raise HTTPException(400, "empty headline")
    source = body.source if body.source in ("crowd", "voice") else "crowd"
    pid = body.player_id if body.player_id and sim.player(body.player_id) else None
    return (await sim.add_news(body.text, source, body.author[:20], body.ticker, pid)).as_dict()


class ChaosIn(BaseModel):
    ticker: str | None = None


@app.post("/api/news/chaos")
async def chaos(body: ChaosIn | None = None):
    try:
        return (await sim.newsroom(body.ticker if body else None)).as_dict()
    except Exception as e:
        raise HTTPException(502, f"newsroom failed: {e}")


class RealIn(BaseModel):
    query: str = "AI chip stocks NVIDIA semiconductor"


@app.post("/api/news/real")
async def real_news(body: RealIn):
    try:
        titles = await tavily_headlines(body.query, 1)
    except Exception as e:
        raise HTTPException(502, f"tavily failed: {e}")
    if not titles:
        raise HTTPException(404, "no headlines found")
    return (await sim.add_news(titles[0], "real", "Tavily")).as_dict()


class ControlIn(BaseModel):
    action: str
    minutes: float = 5
    message: str = ""
    code: str = ""
    ticker: str | None = None


@app.post("/api/control")
async def control(body: ControlIn):
    a = body.action
    if a in ("resume", "toggle_pause") and sim.round_over:
        sim.round_minutes = 0  # back to free play with the final positions
        sim.paused = False
    elif a == "pause":
        sim.paused = True
    elif a == "resume":
        sim.paused = False
    elif a == "toggle_pause":
        sim.paused = not sim.paused
    elif a == "demo":
        await sim.demo()
    elif a == "start_round":
        await sim.start_round(min(30.0, max(1.0, body.minutes)))
    elif a == "reset":
        sim.reset()
        await hub.send_all({"type": "reset", "data": {**sim.snapshot(), **sim.history()}})
    elif a == "toggle_autopilot":
        sim.autopilot = not sim.autopilot
    elif a == "notify":
        message = " ".join(body.message.split())[:160]
        if not message:
            raise HTTPException(400, "empty notification")
        await sim.notify_players(message)
    elif a == "keypad":
        code = body.code.strip().upper()
        if code not in ("CRASH", "FREEZE"):
            raise HTTPException(400, "unknown game keypad code")
        await sim.game_keypad(code, body.ticker)
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True, "paused": sim.paused, "autopilot": sim.autopilot}


class JoinIn(BaseModel):
    name: str


@app.post("/api/join")
def join(body: JoinIn):
    a = sim.join(body.name)
    return {"id": a.id, "name": a.name, **sim.portfolio(a.id)}


class TradeIn(BaseModel):
    id: str
    side: str
    qty: int | str
    ticker: str = "GPU"


@app.post("/api/trade")
async def trade(body: TradeIn):
    if body.side not in ("buy", "sell"):
        raise HTTPException(400, "side must be buy or sell")
    if sim.player(body.id) is None:
        raise HTTPException(404, "unknown player, join again")
    try:
        qty = body.qty if body.qty == "all" else max(1, int(body.qty))
        item = await sim.human_trade(body.id, body.side, qty, body.ticker)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"filled": item.filled, "fill_price": item.fill_price, "ticker": item.ticker, **sim.portfolio(body.id)}


@app.get("/api/explain/{ticker}")
async def explain(ticker: str):
    """Market forensics: why did this stock just move?"""
    return await sim.explain(ticker, 30.0)


@app.get("/api/me/{pid}")
def me(pid: str):
    if sim.player(pid) is None:
        raise HTTPException(404, "unknown player")
    return sim.portfolio(pid)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "init", "data": {**sim.snapshot(), **sim.history()}}, default=str))
    hub.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(websocket)


# MCP Streamable HTTP lives at /mcp; mounted last so the routes above win.
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=_mcp_security())
app.mount("/", mcp_app)


def main():
    import uvicorn

    uvicorn.run("flashcrash.server:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
