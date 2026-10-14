# ⚡ Flash Crash

**Your family vs. 300 AI traders, live on the living-room TV.**

Flash Crash is a party game: a stock market for one fake company, **$GPU**, run by hundreds of LLM agents with
personalities, including Reddit degens, Buffett-style value investors, panic-selling retirees, momentum quants,
conspiracy theorists and hedge-fund whales. The market plays out on the **Fire TV**. Everyone in the room scans a QR code and
trades against the AIs from their phone, or **leaks fake breaking news** ("GPU CEO arrested at the airport") by typing or
speaking it. Every agent reads the headline, argues about it on its timeline, and trades. Bubbles, panics and flash crashes
emerge on their own. **Alexa+** (or any AI agent) can join in over **MCP**: *"Alexa, leak that GPU just won a NASA contract."*

![TV dashboard](docs/tv.png)

## How it works

```
 phones (/play) ──trade, leak news, voice──┐
 Fire TV app (WebView, D-pad) ── /tv ──────┤        ┌──────────── Simulation ─────────────┐
 Alexa+ / agents ── MCP /mcp ──────────────┼──▶ FastAPI ─▶ 300 agents ─▶ LLM brain (async pool)
                                           │        │      │ persona + portfolio + news +    │
 Tavily real headlines ────────────────────┘        │      │ "timeline" of other agents     │
                                                    │      ▼                                 │
                                                    │  price-time-priority order book        │
                                                    │  + market maker, 2s candles, analytics │
                                                    └──────────── WebSocket push ────────────┘
```

* **Agents** (`flashcrash/personas.py`): 11 archetypes. Each agent has its own name, bankroll, risk appetite, private
  fair-value belief and short memory of its last thoughts.
* **Brain** (`flashcrash/brain.py`): each turn, an agent receives the market (price path, best bid/ask), its portfolio, the latest
  headlines and three posts from other agents' timelines, which is what makes herding contagious. It replies with JSON:
  `{"action","qty","price","thought"}`. Most agents run on a fast model; whales use a bigger reasoning model. A 24-wide async
  worker pool streams decisions into the market continuously.
* **Exchange** (`flashcrash/market.py`): a real limit order book with price-time priority, cash and share reservations,
  no shorting and no self-trading. A market maker quotes around a slow-moving average, so noise is absorbed but news gets through.
* **Science panel**: the footer tracks *stylized facts* of real markets as they emerge: fat tails (excess kurtosis),
  volatility clustering, drawdown and a herding index.

## Quick start

```bash
uv sync
cp .env.example .env        # add a key, or leave empty for offline mock mode
uv run flash-crash          # http://localhost:8000/tv  ·  phones: scan the QR code
```

With no API key, agents use rule-based persona brains (badge: **OFFLINE MOCK**), so the full game works offline.

### AI providers (OpenAI-compatible, pick one)

| Provider | Env | Default models |
|---|---|---|
| **Nebius Token Factory**: NVIDIA Nemotron | `NEBIUS_API_KEY` | `nemotron-3-nano-30b-a3b` (crowd), `nemotron-3-super-120b-a12b` (whales). Exact IDs are resolved from the live `/models` list |
| **Amazon Bedrock** | `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION` | `openai.gpt-oss-20b-1:0`, `openai.gpt-oss-120b-1:0` |
| Any OpenAI-compatible API | `FLASHCRASH_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_API_KEY`, `FAST_MODEL` | |

`TAVILY_API_KEY` enables the **🌍 Real news** button, which injects a live real-world AI-chip headline into the market.

## Fire TV

The `firetv/` app is a native Fire OS (Android) app: a full-screen WebView tuned for the 10-foot UI.

* **D-pad** ◀ ▶ moves between controls; **OK** fires the selected one
* **Play/Pause** pauses the market, **Fast-forward** asks the AI newsroom for a chaos headline, **Rewind** starts a new round
* **Menu** opens server settings

```bash
# Prebuilt: dist/flash-crash-firetv.apk
# Build & sideload over Wi-Fi (enable ADB debugging on the TV first):
scripts/firetv.sh <fire-tv-ip> http://<this-computer-ip>:8000
```

The TV and phones must reach the server: use the same Wi-Fi, or set `PUBLIC_URL` to a tunnel or deployed URL.

## Alexa+ / MCP

The server exposes an MCP endpoint (spec 2025-11-25+, **Streamable HTTP**) at `/mcp` with these tools:

| Tool | What it does |
|---|---|
| `market_status` | price, move today, who's winning, latest headline |
| `leak_news` | inject a headline that every agent reacts to |
| `chaos` | ask the AI newsroom for a surprise headline |
| `trade` | buy or sell for a named human player |
| `control` | pause / resume / reset / autopilot news |

Connect any MCP client to `http://<host>:8000/mcp`. DNS-rebinding protection only accepts requests addressed to localhost,
the LAN IP, `PUBLIC_URL` or `MCP_ALLOWED_HOSTS`.

## Deploy

`docker build -t flash-crash . && docker run -p 8000:8000 --env-file .env flash-crash`

## Tests

`uv run pytest`: order book invariants (cash and share conservation across 3,000 random orders), reply parsing for
messy LLM output, and an end-to-end simulation run.

## Security notes

Flash Crash is a party game meant for a home network: the phone and TV endpoints have no authentication by design. Don't
expose it to the open internet without putting auth in front. API keys stay on the server, and provider errors are reduced to
the error type before being shown on screen.

## License

MIT. Charts are rendered with [TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/) (Apache-2.0).
