"""Trader archetypes. Each AI agent is one of these plus a random name, bankroll and quirks."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

TICKER = "GPU"
COMPANY = "Green Processing Unlimited"


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    emoji: str
    description: str
    weight: float  # how often this archetype gets a turn
    count: int  # share of the population
    cash: tuple[int, int]
    shares: tuple[int, int]
    # mock-brain knobs: reaction to momentum, to news, to deviation from fair value, randomness
    momentum: float = 0.0
    news: float = 1.0
    value: float = 0.0
    noise: float = 0.3
    size: float = 0.15  # fraction of buying power per trade
    whale: bool = False


ARCHETYPES: list[Archetype] = [
    Archetype("wsb", "WSB Degen", "🦍", "A Reddit YOLO trader. Loves rockets, memes and leverage. FOMOs into anything going up, diamond-hands until it hurts. Thinks 'buy the dip' is a personality.",
              1.4, 60, (2_000, 15_000), (0, 40), momentum=1.6, news=1.4, noise=0.6, size=0.5),
    Archetype("value", "Value Investor", "🧓", "A patient Buffett-style value investor. Has a fair value estimate and buys when others are fearful, sells when others are greedy. Mocks hype.",
              0.6, 30, (20_000, 80_000), (50, 300), momentum=-0.3, news=0.4, value=2.0, noise=0.15, size=0.2),
    Archetype("panic", "Panic Seller", "😱", "An anxious retiree who checks the price every minute. Any bad news or red candle triggers a sell-everything reflex. Buys back near the top.",
              1.0, 35, (5_000, 30_000), (40, 200), momentum=0.8, news=2.0, noise=0.3, size=0.4),
    Archetype("quant", "Momentum Quant", "🤖", "A cold systematic trend follower. Speaks in z-scores. Buys strength, sells weakness, ignores narratives.",
              1.2, 30, (20_000, 60_000), (20, 150), momentum=2.2, news=0.2, noise=0.1, size=0.25),
    Archetype("contrarian", "Contrarian", "🙃", "Does the opposite of the crowd on principle. Sells euphoria, buys panic, smug about it either way.",
              0.9, 25, (10_000, 40_000), (20, 150), momentum=-1.5, news=-0.6, noise=0.25, size=0.25),
    Archetype("doomer", "Doomer", "☠️", "Has predicted 14 of the last 2 crashes. Sees every headline as proof the bubble is about to pop. Rarely buys.",
              0.7, 20, (10_000, 40_000), (10, 80), momentum=0.3, news=1.2, value=0.5, noise=0.2, size=0.3),
    Archetype("evangelist", "AI Evangelist", "🚀", "A perma-bull tech evangelist who thinks GPU will be a $10 trillion company. Buys every dip, reads every headline as bullish.",
              1.0, 30, (5_000, 30_000), (10, 100), momentum=0.5, news=0.6, noise=0.3, size=0.35),
    Archetype("daytrader", "Day Trader", "⚡", "A hyperactive scalper with six monitors. Trades the spread, takes quick profits, never holds a position long.",
              1.6, 30, (10_000, 30_000), (20, 100), momentum=0.9, news=0.8, noise=0.5, size=0.2),
    Archetype("conspiracy", "Conspiracy Theorist", "🛸", "Believes the market is rigged by lizard-people market makers. Reads hidden meaning in every headline and often reacts in the opposite direction to everyone else.",
              0.8, 20, (2_000, 20_000), (0, 60), momentum=0.0, news=-1.0, noise=0.8, size=0.4),
    Archetype("boomer", "Index Boomer", "⛳", "A calm long-term investor who buys a little every month and never sells. Mildly confused by all the excitement.",
              0.4, 15, (10_000, 50_000), (50, 200), momentum=0.0, news=0.1, noise=0.05, size=0.05),
    Archetype("whale", "Hedge Fund Whale", "🐋", "A ruthless macro hedge fund manager with enormous size. Thinks in narratives and second-order effects; happily moves the market to trap retail.",
              0.35, 6, (800_000, 2_000_000), (3_000, 10_000), momentum=0.4, news=1.0, value=0.8, noise=0.3, size=0.12, whale=True),
]

BY_KEY = {a.key: a for a in ARCHETYPES}

FIRST = "Ava Ben Chad Dee Eli Fay Gus Hal Ivy Jax Kai Lou Max Nia Oz Pia Quinn Rex Sam Tia Uma Vic Wes Xan Yui Zed".split()
HANDLE_BITS = "moon tendies stonk bear bull alpha sigma chart yolo hodl apes rocket candle macro vol gamma theta".split()


@dataclass
class Agent:
    id: str
    name: str
    handle: str
    archetype: Archetype
    fair_value: float
    risk: float  # 0.5..1.5 multiplier
    memory: list[str] = field(default_factory=list)  # last thoughts
    busy: bool = False
    human: bool = False


def spawn_agents(n: int, initial_price: float, rng: random.Random) -> list[tuple[Agent, int, int]]:
    """Return (agent, cash, shares) tuples with the population split by archetype count."""
    total = sum(a.count for a in ARCHETYPES)
    out = []
    i = 0
    for arch in ARCHETYPES:
        k = max(1, round(n * arch.count / total)) if arch.whale else round(n * arch.count / total)
        for _ in range(k):
            i += 1
            name = rng.choice(FIRST)
            handle = f"{rng.choice(HANDLE_BITS)}_{rng.choice(HANDLE_BITS)}{rng.randint(1, 99)}"
            agent = Agent(
                id=f"a{i}",
                name=name,
                handle=handle,
                archetype=arch,
                fair_value=round(initial_price * rng.uniform(0.75, 1.3), 2),
                risk=rng.uniform(0.5, 1.5),
            )
            cash = rng.randint(*arch.cash)
            # hold roughly as much stock as cash so the crowd isn't structurally biased to buy
            shares = max(rng.randint(*arch.shares), int(cash / initial_price * rng.uniform(0.6, 1.2)))
            out.append((agent, cash, shares))
    return out
