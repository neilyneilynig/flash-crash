# Submission kit

Everything needed to submit Flash Crash to both hackathons: the Devpost write-ups, a 3-minute video script built
around demo mode, and a checklist.

| | Amazon Developer Hackathon | Nebius x NVIDIA Global AI Hackathon |
|---|---|---|
| Deadline | **Oct 23, 2026, 12:00 PT** | **Oct 30, 2026, 10:00 PT** |
| Track | Fire TV (primary) + Alexa+ MCP, Open Source mini challenge | Best Apps & Agents (+ Tavily prize) |
| Must show | Running on a real Fire TV (or simulator) | Runtime calls to Nebius Token Factory with an NVIDIA model |
| Run with | any provider (Bedrock works too) | `NEBIUS_API_KEY` set: badge reads **NVIDIA NEMOTRON · NEBIUS** |

---

## Tagline

**Your family vs. 300 AI traders, live on the living-room TV. Leak fake news, move the market, and try not to get caught by the AI SEC.**

## Inspiration

Markets are the biggest multiplayer game on Earth, and they're driven by stories: a headline drops, the crowd reacts, the
crowd reacts to the crowd. We wanted to put that whole feedback loop in a living room, where you can *be* the headline,
and to use LLM agents for what they're actually great at: playing hundreds of distinct, believable people at once.

## What it does

* A Fire TV app shows a live stock market with four fake companies: $GPU (AI chips), $ROBO (robotaxis), $NOVA (fusion
  power) and $MEME (Doge Burger, a meme stock).
* **300 AI traders** with 11 personalities (Reddit degens, value investors, panic sellers, momentum quants, conspiracy
  theorists, hedge-fund whales...) each with their own bankroll, favourite stocks, beliefs and posting quirk, trade on a
  real limit order book and argue on a live timeline.
* Everyone in the room **scans a QR code** and trades from their phone. Or they **leak breaking news**, typed or spoken:
  "Doge Burger adds a $1,000 golden burger". The headline is routed to the company it names, every AI watching reads it,
  and bubbles, panics and flash crashes emerge on their own.
* **The AI SEC** watches for insider trading. Buy a stock, then leak good news about it, and if it pays off you may get
  busted on the big screen and fined your profit plus a penalty.
* **Credibility**: the AIs remember who's been crying wolf. Each leak makes them trust you less (it recovers over time),
  and they'll call out serial leakers by name.
* **Circuit breakers** halt a stock that moves 12% in 20 seconds, just like a real exchange.
* **Market forensics**: whenever a stock rips or crashes, an AI analyst explains *why* on screen: the headline that
  triggered it and which trader personalities drove it, with dollar flows.
* **Timed rounds** end on a podium with an **AI sportscaster recap** of the round.
* **Alexa+** joins over MCP: "Alexa, leak that RoboRide got approved in every city", "Alexa, why did MEME crash?",
  "Alexa, start a round".
* **Real news** via Tavily can be injected to see how the AI crowd prices actual headlines.
* The phone controller is a full trading app: chart timeframes, candles or line, SMA/EMA/Bollinger/VWAP/RSI/MACD,
  your trades and average cost marked on the chart, portfolio, leaderboards and achievements.

## How we built it

* **Agents**: a 24-wide async worker pool keeps ~300 personas deciding continuously. Each turn an agent picks a stock
  (favourites, plus attention pulled by fresh news and big moves), receives that stock's price path, the order book, its
  holdings, the relevant headlines and three posts from other agents' timelines, and returns strict JSON
  `{action, qty, price, thought}`. The timeline is what makes herding contagious.
* **Models**: on Nebius Token Factory, the crowd runs on **NVIDIA Nemotron 3 Nano** (thinking disabled for latency) and the
  whales on **Nemotron 3 Super** with reasoning. The same code runs on Amazon Bedrock or any OpenAI-compatible endpoint, and
  offline with rule-based persona brains so the game never breaks at a party.
* **Five AI roles**: traders, the chaos newsroom, the AI SEC, the forensics analyst and the round sportscaster.
* **Reliability**: the Nemotron path is covered by tests against a fake OpenAI-compatible server, and every AI role
  falls back to a template if the provider hiccups, so a party never stalls.
* **Exchange**: a real price-time-priority limit order book per stock with cash/share reservations, no shorting or
  self-trading, one wallet per trader shared across books, and market makers quoting around a slow EMA so noise is absorbed
  but news gets through. Property tests check cash and share conservation across thousands of random orders.
* **Science panel**: the TV tracks stylized facts of real markets as they emerge: fat tails (excess kurtosis), volatility
  clustering, drawdown and a herding index.
* **Stack**: Python, FastAPI, WebSockets, the MCP Python SDK (Streamable HTTP, spec 2025-11-25), TradingView Lightweight
  Charts, a native Fire OS WebView app with D-pad and media-key controls, Docker.

## Challenges we ran into

* **Calibration.** Our first market drifted up forever and swung wildly. Real markets don't. We added momentum dead zones,
  balanced starting portfolios, an EMA-anchored market maker and a news-driven fundamental anchor, until calm stretches
  stayed calm and a headline produced the classic overshoot-then-settle shape.
* **300 voices that don't sound like one.** Personas, per-agent quirks, reactions to specific headlines, replies to other
  traders and a no-repeat filter: 99% of posts in a test run were unique.
* **A readable TV.** Hundreds of decisions per second become a curated trickle: human players first, then the stock on
  screen, then big trades.
* **10-foot UI.** Everything had to be operable with a Fire TV remote, legible from a sofa and robust to Wi-Fi hiccups.

## Accomplishments we're proud of

* Emergent bubbles and crashes from agents, not scripted animations.
* The AI SEC moment: the room screams when someone gets busted.
* It works fully offline, so the demo can't be killed by an API outage.

## What we learned

LLM agents are at their best as a *crowd*: individually simple, collectively surprising. And the most fun AI features
are the ones that react to what the humans just did.

## What's next

Persistent leagues, sector ETFs and options, agents that remember players across rounds ("I'm not falling for Neil's
fake news again"), and a narrated replay you can share as a short video.

---

## 3-minute video script (demo mode)

Start the server with your Nebius key, open the Fire TV app, and have one phone joined. Start the show with
**"Alexa, ask Flash Crash to run the demo"** (MCP `control` → `demo`), or `curl -XPOST localhost:8000/api/control
-H 'content-type: application/json' -d '{"action":"demo"}'`, or press **D** on a keyboard on the TV page.
Demo mode plays a fixed timeline so every take hits the same beats:

| Time | On screen | Say |
|---|---|---|
| 0:00 | TV close-up, ticker strip moving | "This is a stock market. Every trader in it is an AI. 300 of them, running on NVIDIA Nemotron on Nebius." |
| 0:12 | Timeline posts scrolling | "Each one has a personality. Degens, value investors, panic sellers, whales. They read the news and each other." |
| 0:25 | Phone scans the QR code, tutorial | "Anyone in the room can join from their phone." |
| 0:35 | Demo headline: golden burger, $MEME rips | "And you can leak the news. Watch the meme stock." |
| 0:50 | 🔍 WHY card | "Our AI analyst explains the move: the headline, and which personalities piled in." |
| 1:05 | Phone: buy $ROBO, then leak good RoboRide news | "So obviously I'll buy first, then leak good news..." |
| 1:30 | 🚨 AI SEC INVESTIGATION banner | "...which is insider trading. There's an AI SEC. Busted." |
| 1:45 | Alexa: "why did MEME crash?" | "Alexa can play too, over MCP." |
| 2:00 | Phone: indicators sheet, chart markers | "The phone is a real trading app: indicators, your trades on the chart." |
| 2:15 | Science panel close-up | "The fat tails and volatility clustering aren't programmed. They emerge from the agents, like in real markets." |
| 2:30 | Podium + AI sportscaster recap | "Rounds end on a podium, with an AI recap." |
| 2:50 | Logo | "Flash Crash. Your family versus 300 AIs." |

Record the TV with a capture card or a phone on a tripod. The Amazon rules want the Fire TV app visible on a real device
or simulator.

## Checklist

- [ ] `NEBIUS_API_KEY` in `.env`; badge shows **NVIDIA NEMOTRON · NEBIUS**; tokens/s ticking (Nebius entry)
- [ ] `TAVILY_API_KEY` in `.env`; **🌍 Real news** button visible (Tavily prize)
- [ ] Fire TV app installed (`scripts/firetv.sh <tv-ip> http://<server-ip>:8000`) and shown in the video (Amazon entry)
- [ ] Alexa+ connected to `http://<public-url>/mcp` (needs a public HTTPS URL, e.g. a tunnel; set `PUBLIC_URL`)
- [ ] Public GitHub repo with the MIT license and README
- [ ] Video under 3 minutes, uploaded (YouTube/Vimeo), linked on Devpost
- [ ] Devpost: paste the sections above, add screenshots (`docs/tv.png`), list built-with tags
- [ ] Feedback form (Nebius asks for product feedback)
