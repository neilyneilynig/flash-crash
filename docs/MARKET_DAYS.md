# Market Days

Market Days is separate from the party game at `/days`: a long-running game on **real stocks**. Humans and a desk of
AI fund managers each start with $100,000 of virtual cash and compete on a leaderboard.

* **The AI desk**: 🦍 Chad the Degen, 🧓 Warren Jr., 🤖 Q-Bot 9000 and 🐋 Moby Capital use the same persona brains as the
  party game (NVIDIA Nemotron on Nebius when `NEBIUS_API_KEY` is set). On each AI cycle every manager reads the live
  Alpaca quote, the last 10 daily bars, its own book and (with `TAVILY_API_KEY`) fresh headlines, decides in character,
  and places a matching **Alpaca paper** order. Their reasoning is logged next to the order id.
* **Humans** join a game by name and trade at the real ask (buy) or bid (sell). Human trades are virtual fills only.
* **Ledger**: every fill is stored in `market_days_fills`; cash, positions, average cost, equity and rank are always
  derived from it.
* **Autopilot**: with `MARKET_DAYS_AUTOPILOT_MINUTES=15` the desk trades every 15 minutes while Alpaca's clock says the
  market is open.
* **Local play**: without `DATABASE_URL` games live in memory; without Alpaca keys quotes are simulated. The page shows
  both clearly, so you can try the whole thing with no accounts.

## Safety

* Orders only go to an Alpaca **paper** endpoint: if `ALPACA_PAPER_BASE_URL` isn't `paper-api.*`, AI orders are refused.
* AI orders are capped at `MARKET_DAYS_MAX_ORDER_QTY` shares (default 25).
* Creating games and running AI cycles place paper orders, so when Alpaca keys are set they require
  `MARKET_DAYS_ADMIN_TOKEN` (sent as the `X-Admin-Token` header; the page asks for it). Joining and human trades are open.
* A database outage disables Market Days without taking the party game down.

## Railway backend

Deploy this repository with the included `railway.toml` and attach a PostgreSQL service. Set these Railway variables:

```sh
DATABASE_URL=postgresql://...                 # supplied by Railway PostgreSQL
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
MARKET_DAYS_ALLOWED_ORIGINS=https://your-vercel-app.vercel.app
MARKET_DAYS_ADMIN_TOKEN=...                   # any long random string; required for AI cycles with Alpaca keys
MARKET_DAYS_AUTOPILOT_MINUTES=15              # optional: AI desk trades on its own during market hours
MARKET_DAYS_MAX_ORDER_QTY=25                  # optional: cap per AI paper order
```

Do not put these values in source control or the Vercel project. The backend creates `market_days_games` and
`market_days_events` on startup. Visit `https://<railway-domain>/days` for the backend-hosted MVP.

## Vercel frontend

Deploy the `market-days/` directory as a Vercel project. Set only:

```sh
MARKET_DAYS_API_URL=https://<railway-domain>
```

`market-days/index.html` is an identical copy of `flashcrash/static/market_days.html` (a test enforces it). On Vercel
it reads the backend URL from `/api/config`; on Railway it talks to its own origin. The Vercel UI calls the Railway API; the backend also emits Market Days updates on its existing `/ws` stream. Configure
the backend CORS origin above. The party game endpoints and `/play` remain unchanged.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/days/config` | storage, quote source, safety settings, AI desk |
| GET | `/api/days/games` | recent games |
| POST | `/api/days/games` | create `{name, symbols}` (operator token with Alpaca keys) |
| GET | `/api/days/games/{id}` | game, quotes, leaderboard with positions, last 100 events |
| POST | `/api/days/games/{id}/join` | `{name}` returns your player id |
| POST | `/api/days/games/{id}/trade` | `{player_id, symbol, side, qty}` filled at bid/ask |
| POST | `/api/days/games/{id}/ai-cycle` | `{symbol?}` every AI manager decides and trades (operator token with Alpaca keys) |

Live updates arrive on the shared `/ws` stream as `{"type": "market_days", "game_id", "event"}`.
