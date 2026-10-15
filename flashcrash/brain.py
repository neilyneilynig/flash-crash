"""Agent brains: an LLM brain (any OpenAI-compatible endpoint) and an offline mock brain.

Providers (env FLASHCRASH_PROVIDER):
  nebius  - Nebius Token Factory serving NVIDIA Nemotron
  bedrock - Amazon Bedrock's OpenAI-compatible endpoint
  openai  - anything else OpenAI-compatible (LLM_BASE_URL / LLM_API_KEY)
  mock    - rule-based personas, no network (default when no key is set)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass

from .chatter import Chatter
from .personas import BY_TICKER, STOCKS, Agent


@dataclass
class Decision:
    action: str  # buy | sell | hold
    qty: int
    price: float | None  # None = market order
    thought: str
    model: str = ""
    latency: float = 0.0


@dataclass
class BrainStats:
    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_total: float = 0.0
    started: float = 0.0
    last_error: str = ""

    def snapshot(self) -> dict:
        elapsed = max(1.0, time.time() - self.started)
        return {
            "calls": self.calls,
            "errors": self.errors,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "tokens_per_s": round((self.prompt_tokens + self.completion_tokens) / elapsed, 1),
            "calls_per_min": round(self.calls / elapsed * 60, 1),
            "avg_latency": round(self.latency_total / self.calls, 2) if self.calls else 0,
            "last_error": self.last_error[-160:],
        }


SYSTEM = """You are {name} (@{handle}), a trader in a live stock market with these stocks:
{listing}
Your persona: {persona}
Your posting style: {quirk}.
Stay fully in character. Decide your next trade in ${ticker} from the market data, the news and your timeline.
Your post must be unique and specific: react to the latest headline, the price move, or reply to another trader by @handle.
Never repeat your previous posts.
Reply with ONLY a JSON object, no prose, no markdown:
{{"action":"buy"|"sell"|"hold","qty":<integer shares>,"price":<limit price number or null for market order>,"thought":"<your in-character post, max 25 words>"}}"""

LISTING = "\n".join(f"- ${st.ticker}: {st.company}, {st.blurb}" for st in STOCKS)


def build_messages(agent: Agent, ctx: dict) -> list[dict]:
    a = agent.archetype
    tk = ctx["ticker"]
    ref = BY_TICKER[tk].price if tk in BY_TICKER else ctx.get("ref_price", ctx["price"])  # real stocks: recent average
    fair = f" You privately think ${tk} is worth about ${ctx['fair'] * ref:.2f}." if a.value > 0 else ""
    system = SYSTEM.format(name=agent.name, handle=agent.handle, listing=ctx.get("listing", LISTING), persona=a.description + fair,
                           quirk=agent.quirk or "plain", ticker=tk)
    news = "\n".join(f"- [{n['age']}s ago] {n['text']}" + (f" (leaked by {n['author']}, who keeps leaking news; "
                      f"credibility {n['cred']:.0%})" if n.get("cred", 1) < 0.8 else "")
                      for n in ctx["news"]) or "- (nothing new)"
    timeline = "\n".join(f"- @{p['handle']} on ${p['ticker']}: {p['text']}" for p in ctx["timeline"]) or "- (quiet)"
    memory = " | ".join(agent.memory[-3:]) or "(none)"
    holdings = ", ".join(f"{q} ${t}" for t, q in ctx["holdings"].items() if q) or "nothing"
    user = f"""NOW TRADING ${tk}: last ${ctx['price']:.2f} ({ctx['change_pct']:+.1f}% today), bid {ctx['bid']} / ask {ctx['ask']}
Recent closes (oldest->newest): {', '.join(f'{p:.2f}' for p in ctx['closes'])}
YOU: cash ${ctx['cash']:,.0f}, own {holdings}. Total P&L {ctx['pnl_pct']:+.1f}%. Max buy ~{ctx['max_buy']} ${tk}, max sell {ctx['max_sell']}.
NEWS:
{news}
YOUR TIMELINE:
{timeline}
Your last posts (don't repeat them): {memory}
Your move on ${tk}?"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_THINK = re.compile(r"<think>.*?</think>", re.S)
_JSON = re.compile(r"\{.*\}", re.S)


def parse_decision(text: str) -> Decision:
    text = _THINK.sub("", text or "")
    m = _JSON.search(text)
    if not m:
        raise ValueError(f"no JSON in reply: {text[:80]!r}")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # models sometimes emit several objects or trailing junk; take the first balanced one
        depth, end = 0, None
        for i, ch in enumerate(raw):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                end = i + 1
                break
        data = json.loads(raw[:end])
    action = str(data.get("action", "hold")).lower().strip()
    if action not in ("buy", "sell", "hold"):
        action = "hold"
    try:
        qty = max(0, int(float(data.get("qty") or 0)))
    except (TypeError, ValueError):
        qty = 0
    price = data.get("price")
    try:
        price = float(price) if price not in (None, "", "null", "market") else None
    except (TypeError, ValueError):
        price = None
    thought = str(data.get("thought", "")).strip()[:160]
    return Decision(action, qty, price, thought)


PROVIDERS = {
    "nebius": {
        "base_url": "https://api.tokenfactory.nebius.com/v1/",
        "key_env": "NEBIUS_API_KEY",
        "fast": "nvidia/nemotron-3-nano-30b-a3b",
        "big": "nvidia/nemotron-3-super-120b-a12b",
    },
    "bedrock": {
        "base_url": f"https://bedrock-runtime.{os.getenv('AWS_REGION', 'us-west-2')}.amazonaws.com/openai/v1",
        "key_env": "AWS_BEARER_TOKEN_BEDROCK",
        "fast": "openai.gpt-oss-20b-1:0",
        "big": "openai.gpt-oss-120b-1:0",
    },
    "openai": {"base_url": None, "key_env": "LLM_API_KEY", "fast": "", "big": ""},
}


def detect_provider() -> str:
    p = os.getenv("FLASHCRASH_PROVIDER", "").lower()
    if p:
        return p
    for name in ("nebius", "bedrock"):
        if os.getenv(PROVIDERS[name]["key_env"]):
            return name
    return "mock"


class LLMBrain:
    mode = "llm"

    def __init__(self, provider: str, concurrency: int = 24, http_client=None):
        from openai import AsyncOpenAI

        cfg = PROVIDERS[provider]
        self.provider = provider
        self.client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL") or cfg["base_url"],
            api_key=os.getenv(cfg["key_env"]) or os.getenv("LLM_API_KEY", "") or "unset",
            timeout=float(os.getenv("LLM_TIMEOUT", "30")),
            max_retries=1,
            http_client=http_client,
        )
        self.fast_model = os.getenv("FAST_MODEL") or cfg["fast"]
        self.big_model = os.getenv("BIG_MODEL") or cfg["big"] or self.fast_model
        self.sem = asyncio.Semaphore(concurrency)
        self.stats = BrainStats(started=time.time())
        self._no_think = provider == "nebius"

    async def resolve_models(self) -> None:
        """Match configured model ids against the provider's live /models list (ids drift)."""
        try:
            ids = [m.id async for m in self.client.models.list()]
        except Exception as e:  # listing is optional; keep configured ids
            self.stats.last_error = f"models.list failed: {type(e).__name__}"
            return

        def pick(want: str, *hints: str) -> str:
            if want in ids:
                return want
            low = {i.lower(): i for i in ids}
            if want.lower() in low:
                return low[want.lower()]
            for i in ids:
                if all(h in i.lower() for h in hints):
                    return i
            return want

        if self.provider == "nebius":
            self.fast_model = pick(self.fast_model, "nemotron", "nano")
            self.big_model = pick(self.big_model, "nemotron", "super")

    async def complete(self, messages: list[dict], big: bool = False, max_tokens: int = 160) -> tuple[str, str, float]:
        model = self.big_model if big else self.fast_model
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if self._no_think and not big else None
        async with self.sem:
            t0 = time.time()
            try:
                resp = await self.client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens if not big else max_tokens * 6,
                    temperature=0.9, extra_body=extra,
                )
            except Exception as e:
                if extra and "chat_template_kwargs" in str(e):
                    self._no_think = False
                    return await self.complete(messages, big, max_tokens)
                self.stats.errors += 1
                # broadcast to every screen, so keep it to the error type and HTTP status only
                status = getattr(e, "status_code", None)
                self.stats.last_error = type(e).__name__ + (f" (HTTP {status})" if status else "")
                raise
            dt = time.time() - t0
        self.stats.calls += 1
        self.stats.latency_total += dt
        if resp.usage:
            self.stats.prompt_tokens += resp.usage.prompt_tokens or 0
            self.stats.completion_tokens += resp.usage.completion_tokens or 0
        return resp.choices[0].message.content or "", model, dt

    async def decide(self, agent: Agent, ctx: dict) -> Decision:
        text, model, dt = await self.complete(build_messages(agent, ctx), big=agent.archetype.whale)
        d = parse_decision(text)
        d.model, d.latency = model, dt
        return d

    async def write_headline(self, ctx: dict) -> str:
        tone = {
            "upbeat": "Favor a constructive, upbeat, bullish development: innovation, growth, a win or a partnership.",
            "neutral": "Favor a surprising but broadly neutral development.",
            "downside": "Favor a plausible downside development, without being graphic or alarming.",
        }.get(ctx.get("tone"), "Favor a constructive, upbeat, bullish development.")
        idea = ctx.get("idea")
        msgs = [
            {"role": "system", "content": f"You are the chaos editor of a financial newswire covering ${ctx['ticker']} ({ctx['company']}, a {ctx['blurb']}). "
                                           f"Write ONE surprising, funny but plausible breaking-news headline (max 14 words) that names {ctx['company']} or ${ctx['ticker']} and will move the stock. "
                                           + tone + " Reply with the headline only."},
            {"role": "user", "content": f"${ctx['ticker']} at ${ctx['price']:.2f} ({ctx['change_pct']:+.1f}% today). Recent headlines: "
                                         + "; ".join(n["text"] for n in ctx["news"])
                                         + (f"\nPlayer's news idea: {idea}\nTurn that idea into the headline." if idea else "")
                                         + "\nNext headline:"},
        ]
        text, _, _ = await self.complete(msgs, max_tokens=60)
        lines = [ln.strip().strip('"*').strip() for ln in _THINK.sub("", text).splitlines()]
        return next((ln for ln in lines if ln), "Markets in chaos as traders panic")[:140]

    async def narrate(self, system: str, user: str, fallback: str, max_tokens: int = 140) -> str:
        """Short commentary (SEC verdicts, move forensics, round recaps). Never fails: falls back to the template."""
        try:
            text, _, _ = await self.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                             max_tokens=max_tokens)
        except Exception:
            return fallback
        text = " ".join(_THINK.sub("", text).strip().strip('"').split())
        return text[:420] or fallback


# ----------------------------------------------------------------------------------------
# Mock brain: deterministic-ish persona rules so the whole game runs offline.

_POS = """beat beats record surge surges soar soars rally rallies partnership partners approve approved launch launches cut cuts
upgrade upgraded buyback profit profits wins win won breakthrough moon acquire acquires deal boom bullish golden sells sold
celebrate celebrates gain gains raises raised accept accepts added joins smashes stunned faster contract contracts invest
invests stake viral rocket hit hits best skyrocket skyrockets jumps doubles triples mega giant genius loves""".split()
_NEG = """arrested fraud lawsuit sued recall recalls crash crashes miss misses ban banned downgrade downgraded probe hack hacked
hackers breach layoffs resign resigns delay delays fire fires scandal bankrupt bankruptcy collapse collapses investigation
tariff tariffs shortage bearish lake explodes explosion evacuated incident leaked drops dropped regret furious halts halted
fake plunge plunges short worst fails failed failure loses lost recession fears dies dead toxic poison sink sinks""".split()


def sentiment(text: str) -> float:
    words = re.findall(r"[a-z]+", text.lower())
    s = sum(w in _POS for w in words) - sum(w in _NEG for w in words)
    return max(-1.0, min(1.0, s / 2))


class MockBrain:
    mode = "mock"
    provider = "mock"
    fast_model = big_model = "mock-persona-rules"

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.stats = BrainStats(started=time.time())
        self.chatter = Chatter(self.rng)

    async def resolve_models(self) -> None:
        return None

    async def decide(self, agent: Agent, ctx: dict) -> Decision:
        await asyncio.sleep(self.rng.uniform(0.15, 0.8))  # pretend inference latency
        a, r = agent.archetype, self.rng
        closes = ctx["closes"] or [ctx["price"]]
        mom = (closes[-1] / closes[max(0, len(closes) - 6)] - 1) * 12
        mom = max(-1.2, min(1.2, mom - 0.25 if mom > 0.25 else mom + 0.25 if mom < -0.25 else 0.0))  # ignore chop
        news = sum(sentiment(n["text"]) * max(0.0, 1 - n["age"] / 60) * n.get("cred", 1.0) for n in ctx["news"][:3])
        herd = sum(p["side"] for p in ctx["timeline"]) / max(1, len(ctx["timeline"]))
        anchor = ctx["anchor"]
        val = (ctx["fair"] * anchor / ctx["price"] - 1) * 5
        gravity = (anchor / ctx["price"] - 1) * 6  # everyone eventually feels an absurd price
        score = (a.momentum * mom + a.news * news * 1.5 + a.value * val + 0.25 * herd * a.momentum
                 + gravity + r.gauss(0, a.noise * 1.5))
        if a.key == "boomer":
            score = 0.6 + r.gauss(0, 0.3)
        if a.key == "evangelist":
            score += 0.25
        if a.key == "doomer":
            score -= 0.3
        self.stats.calls += 1
        self.stats.latency_total += 0.4
        if abs(score) < 0.35:
            return Decision("hold", 0, None, self.chatter.post(agent, "hold", ctx, sentiment), "mock")
        side = "buy" if score > 0 else "sell"
        frac = min(1.0, a.size * agent.risk * min(2.5, abs(score)))
        qty = int((ctx["max_buy"] if side == "buy" else ctx["max_sell"]) * frac)
        if qty <= 0:  # wants to trade but can't (no cash / no shares): just post
            return Decision("hold", 0, None, self.chatter.post(agent, "hold", ctx, sentiment), "mock")
        price = None
        if r.random() < 0.75:  # mostly limit orders near the touch
            off = r.uniform(-0.004, 0.006) * (1 if side == "buy" else -1)
            price = round(ctx["price"] * (1 + off), 2)
        return Decision(side, qty, price, self.chatter.post(agent, side, ctx, sentiment), "mock")

    async def narrate(self, system: str, user: str, fallback: str, max_tokens: int = 140) -> str:
        return fallback

    async def write_headline(self, ctx: dict) -> str:
        if ctx.get("idea"):
            idea = " ".join(ctx["idea"].split())[:120]
            return f"{ctx['company']}: {idea[:1].upper() + idea[1:]}"[:140]
        pool = MOCK_HEADLINES.get(ctx["ticker"], []) + GENERIC_HEADLINES
        tone = ctx.get("tone", "upbeat")
        if tone == "upbeat":
            pool = [headline for headline in pool if sentiment(headline) > 0] or pool
        elif tone == "downside":
            pool = [headline for headline in pool if sentiment(headline) < 0] or pool
        return self.rng.choice(pool).format(t="$" + ctx["ticker"], co=ctx["company"])


GENERIC_HEADLINES = [
    "{co} CEO arrested at airport over undisclosed crypto fraud",
    "Regulators open fraud probe into {t} accounting",
    "Mystery whale buys 5% stake in {t} overnight",
    "{co} announces $20B buyback and record profit",
    "{co} CFO resigns abruptly, cites 'vibes'",
    "{co} smashes earnings, raises guidance for the year",
    "Hackers breach {co}, customer data leaked",
    "{co} added to the S&P 500, index funds must buy",
    "Short seller publishes 90-page report calling {t} a fraud",
    "Fed surprises markets with emergency rate cut",
    "Global recession fears grow as tariffs bite",
]

MOCK_HEADLINES = {
    "GPU": ["{co} wins $40B contract to power every data center on the Moon",
            "{t} unveils chip 10x faster than anything on Earth, analysts stunned",
            "Factory fire halts {co} chip production for three months",
            "Major customer reportedly drops {t} for rival chips"],
    "ROBO": ["{co} robotaxi drives itself into a lake on live TV",
             "{co} approved to run driverless taxis in every US city",
             "{co} recalls 80,000 robotaxis after they start honking at pigeons",
             "{co} partners with the biggest ride-hailing app for a global launch"],
    "NOVA": ["{co} reactor hits net energy gain, physicists celebrate",
             "{co} test reactor delays launch again, investors furious",
             "{co} signs deal to power the entire national grid",
             "{co} lab evacuated after 'minor' plasma incident"],
    "MEME": ["{co} adds a $1,000 golden burger to the menu, sells out in an hour",
             "A famous billionaire tweets a {co} burger with a rocket emoji",
             "{co} fries recall after customers report they taste like regret",
             "{co} announces it will accept Dogecoin at every drive-thru"],
}


def make_brain(concurrency: int = 24):
    provider = detect_provider()
    if provider == "mock":
        return MockBrain()
    return LLMBrain(provider, concurrency)
