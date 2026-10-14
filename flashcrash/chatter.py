"""Offline voice for mock agents: in-character posts that react to the news, the price and each other.

Every agent has an archetype voice plus a personal quirk, and recent posts are never repeated,
so the timeline reads like 300 different people even without an LLM.
"""

from __future__ import annotations

import random
import re
from collections import deque

# {t} $TICKER  {co} company  {p} price  {chg} move today  {n} news snippet  {h} @handle  {move} ripping/bleeding/...
VOICE: dict[str, dict[str, list[str]]] = {
    "wsb": {
        "buy": ["{t} {chg} and I'm all in, wife's boyfriend approved 🦍", "Sold my car for more {t}. No regrets.",
                "{t} at {p}?? That's free money, apes together strong", "Calls on {t}. Puts on my sleep schedule.",
                "If {t} hits the moon I'm buying a lambo. If not, Wendy's.", "Just YOLO'd my rent into {t} 🚀🚀",
                "Smooth brain says buy {t}. Smooth brain has never been wrong (it has).", "Retail is BACK. {t} to the moon."],
        "sell": ["Paper-handed {t}, don't look at me", "Sold {t}, need money for tendies", "{t} is {move}, I'm tapping out 🏳️",
                 "Wife's boyfriend said sell {t}. He's the brains of the family.", "Rugged on {t} again. Classic me.",
                 "Selling {t} to buy more {other}. Galaxy brain move."],
        "hold": ["Not selling {t}. Can't, actually, forgot my password.", "Diamond hands on {t} 💎🙌"],
    },
    "value": {
        "buy": ["{t} at {p} trades below intrinsic value. Accumulating quietly.", "Mr. Market is depressed about {t}. I am not.",
                "Be greedy when others are fearful. Buying {t}.", "Read the 10-K. {t} is cheap. Nobody reads the 10-K.",
                "Margin of safety on {t} is finally there. Buying a slice.", "{co} at {p}? I'll take that deal all day."],
        "sell": ["{t} at {p} prices in perfection. Trimming.", "Euphoria in {t}. Time to be fearful.",
                 "Selling {t}. Price and value have parted ways.", "{t} {chg}? Thank you, speculators. Taking profit.",
                 "Nothing about {co} justifies {p}. Reducing."],
        "hold": ["Patience is a position. Watching {t}.", "No edge in {t} at {p}. Reading an annual report instead."],
    },
    "panic": {
        "buy": ["OK {t} is going up, I can't miss this AGAIN", "Bought {t} back. Please don't crash please don't crash",
                "My nephew said {t} is safe now. Buying.", "FOMO'd into {t}. My heart rate is 140."],
        "sell": ["SELL SELL SELL {t} IS {move}", "I can't watch {t} anymore, getting out", "{t} {chg}?! My retirement!!",
                 "Dumping {t}. I'll buy back when it's safe (I never do).", "Called my broker crying. Out of {t}.",
                 "That's it. {t} is going to zero. Selling everything."],
        "hold": ["Refreshing {t} every 3 seconds. Not okay.", "Holding {t} but I've stopped breathing."],
    },
    "quant": {
        "buy": ["{t} z-score +2.1 on momentum. Long.", "Signal fired: {t} breakout. Sizing in.", "Trend filter green on {t}. Executing.",
                "{t} realized vol up, trend up. Model says buy.", "Backtest says buy {t} here. Feelings irrelevant."],
        "sell": ["{t} momentum decayed. Flat.", "Stop hit on {t}. Exiting per rules.", "{t} trend broken at {p}. Short-term sell signal.",
                 "Mean reversion window closed on {t}. Selling.", "Risk model flags {t}. De-grossing."],
        "hold": ["{t} signal-to-noise too low. No trade.", "Waiting for {t} to clear the 20-bar range."],
    },
    "contrarian": {
        "buy": ["Everyone hates {t}. Perfect. Buying.", "Timeline is screaming sell {t}, so I'm buying. Obviously.",
                "{t} {chg} and the crowd is panicking. Thanks for the discount.", "When they run, I walk in. Long {t}."],
        "sell": ["Everyone loves {t}. That's my sell signal.", "{t} memes on my feed = top is in. Selling.",
                 "Selling {t} to the people buying {t}. Simple.", "{t} {chg}? The crowd is never right for long."],
        "hold": ["Waiting for the crowd to pick a side on {t}.", "Too much agreement on {t}. Watching."],
    },
    "doomer": {
        "buy": ["Bought a little {t}. Mainly so I can be sad when it crashes.", "Even I'm buying {t}. That's the top signal."],
        "sell": ["{t} is a bubble. I've said it 40 times. I'll say it again.", "{t} {chg}. This is how 1929 started.",
                 "Selling {t}, buying canned food.", "The {t} chart looks like my last relationship. Out.",
                 "Told you so about {t}. Will tell you again tomorrow.", "{co} is a house of cards. Selling the cards."],
        "hold": ["Holding cash and a bunker. {t} is doomed.", "Still waiting for {t} to collapse. Any minute now."],
    },
    "evangelist": {
        "buy": ["{co} is the future. {t} at {p} is a gift.", "Every dip in {t} is a chance to own the future 🚀",
                "{t} will be a $10T company. Buying more.", "The haters don't understand {t}. Accumulating.",
                "This is the iPhone moment for {co}. Long {t}."],
        "sell": ["Trimming {t}, only to buy more later (I promise).", "Rebalancing out of {t}, still a believer 🙏"],
        "hold": ["{t} is a 10-year hold. Zoom out.", "Not selling {t} to anyone at any price."],
    },
    "daytrader": {
        "buy": ["Scalping {t} long, out in 30s", "{t} bid stacking. In.", "Quick long {t}, tight stop.", "Level 2 on {t} is spicy. Buying.",
                "Six monitors all say buy {t}. Who am I to argue."],
        "sell": ["Took 12 cents on {t}. Next.", "Out of {t}, flipping to {other}.", "{t} offer heavy. Flat.", "Scalped {t}. Bell to bell baby."],
        "hold": ["{t} chopping. Sitting on hands.", "No setup on {t}. Coffee break."],
    },
    "conspiracy": {
        "buy": ["They WANT you to sell {t}. So I'm buying.", "The lizards dumped {t} at {p}. I see the pattern. Long.",
                "{t} ticker spells something backwards. Not a coincidence. Buying.", "Market makers can't stop the truth. {t} up."],
        "sell": ["{co} is a front. Do your research. Selling {t}.", "The {t} chart is a message. It says SELL.",
                 "Price of {t} is set by the same people who faked the moon landing.", "Exited {t}. The birds told me."],
        "hold": ["Watching who's REALLY moving {t}...", "{t} too quiet. Suspicious."],
    },
    "boomer": {
        "buy": ["Bought some {t}. My broker says it's a good company.", "Monthly purchase of {t} done. Back to golf ⛳",
                "Buying {t} like I have every month since 1987.", "Is {t} the one with the computers? Buying."],
        "sell": ["Had to sell a little {t} for the boat payment.", "Grandson said sell {t}. Fine."],
        "hold": ["What's all the fuss about {t}? Holding.", "Long-term investor. Not reading the news."],
    },
    "whale": {
        "buy": ["Accumulating {t} through dark pools. Retail will chase at {p}+.", "Building a position in {t}. Size.",
                "Our desk is long {t}. Narrative turning.", "Bought a block of {t}. They'll find out later."],
        "sell": ["Distributing {t} into retail strength.", "Unloading {t} size. Thanks for the liquidity.",
                 "Our desk is out of {t}. Second-order effects ugly.", "Sold a block of {t} at {p}. Let them hold the bag."],
        "hold": ["Waiting for retail to show their hand on {t}.", "{t} positioning crowded. Patience."],
    },
}

# Reactions to a fresh headline: (my side, news mood)
REACT: dict[tuple[str, str], list[str]] = {
    ("buy", "good"): ["{n}?! Loading {t} 🚀", "Did you see {n}? Buying {t} before everyone reads it.", "{n}. Yeah I'm buying {t}.",
                      "Headline says {n}. {t} is going higher."],
    ("buy", "bad"): ["{n}? Overreaction. Buying the {t} dip.", "Everyone's panicking over {n}. Buying {t}.",
                     "{n} is already priced in. Long {t}."],
    ("sell", "bad"): ["{n}. I'm out of {t}.", "{n}!!! Selling {t} NOW", "After {n}, {t} is done.", "{n}? Nope. Dumping {t}."],
    ("sell", "good"): ["{n} = sell the news. Selling {t}.", "Buy the rumor, sell {n}. Out of {t}.",
                       "{n}? Too good to be true. Selling {t}."],
    ("buy", "meh"): ["{n}... I'll take that as bullish for {t}.", "Not sure what {n} means but I'm buying {t}."],
    ("sell", "meh"): ["{n}? Feels bad for {t}. Selling.", "No idea what {n} means for {t}. Selling to be safe."],
}

# When a serial leaker strikes again: {a} is the leaker
SKEPTIC = ["{a} again? Not falling for it this time.", "Another 'scoop' from {a}. Sure, buddy.",
           "{a} cried wolf one too many times. Ignoring.", "Reminder: {a} has leaked like 5 headlines today. Holding.",
           "Is {a} ok? That's the third 'breaking news' this minute.", "I don't trade on {a}'s fan fiction."]

AGREE = ["@{h} exactly what I was thinking", "@{h} right there with you", "@{h} great minds", "@{h} 100%. Same trade.",
         "@{h} finally someone gets it"]
DISAGREE = ["@{h} you're going to regret that", "@{h} thanks for the liquidity 😘", "@{h} bold move. Wrong, but bold.",
            "@{h} I'll take the other side of that", "@{h} NGMI"]

EMOJI = "🚀 💎 🔥 📉 📈 😭 🤡 🐂 🐻 💸 👀 😤 🙏 🧠 ⚡".split()
SPORTS = ["Full-court press.", "That's a hat trick.", "4th and long, going for it.", "Swing for the fences.", "Back of the net."]
SUNTZU = ["As Sun Tzu said: in the midst of chaos, there is opportunity.", "Sun Tzu: appear weak when you are strong.",
          "Sun Tzu: let your plans be dark as night."]
GRANDMA = ["Grandma always said never trust a candle.", "Grandma would be proud.", "Grandma says buy what you know."]
GAMER = ["GG.", "this is my final boss", "skill issue tbh", "respawning with more cash", "speedrunning bankruptcy"]
CAT = ["Mr. Whiskers knocked my phone and placed this trade.", "Mr. Whiskers approves.", "Mr. Whiskers is disappointed in me."]


def _snip(text: str, words: int = 6) -> str:
    w = text.split()
    return '"' + " ".join(w[:words]) + ("…" if len(w) > words else "") + '"'


def _quirk(text: str, quirk: str, side: str, rng: random.Random) -> str:
    if "CAPS" in quirk:
        return text.upper() if side != "hold" else text
    if "emojis" in quirk:
        return text + " " + "".join(rng.sample(EMOJI, 3))
    if "financial advice" in quirk:
        return text + " (not financial advice)"
    if "cat" in quirk:
        return rng.choice(CAT) + " " + text
    if "sports" in quirk:
        return text + " " + rng.choice(SPORTS)
    if "Sun Tzu" in quirk:
        return text + " " + rng.choice(SUNTZU)
    if "Fed" in quirk:
        return text + (" Thanks, Fed." if side == "sell" else " The Fed can't stop this.")
    if "lowercase" in quirk:
        return re.sub(r"[.!?,;:]", "", text.lower())
    if "hashtags" in quirk:
        return text + " #" + ("bullish" if side == "buy" else "bearish") + " #stonks"
    if "pirate" in quirk:
        return "Arr! " + text.replace("my ", "me ").replace("you", "ye") + " ☠️"
    if "WAGMI" in quirk:
        return text + (" WAGMI" if side == "buy" else " NGMI")
    if "documentary" in quirk:
        return "Here we observe the trader in its natural habitat. " + text
    if "out of 10" in quirk:
        return text + f" {rng.randint(1, 11)}/10."
    if "calm" in quirk:
        return text.replace("!", ".").replace("?!", ".") + " Breathing deeply."
    if "grandma" in quirk:
        return text + " " + rng.choice(GRANDMA)
    if "gamer" in quirk:
        return text + " " + rng.choice(GAMER)
    return text


class Chatter:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.recent: deque[str] = deque(maxlen=400)
        self._seen: set[str] = set()

    def post(self, agent, side: str, ctx: dict, mood_of) -> str:
        """An in-character, non-repeating post. mood_of(text) -> sentiment in [-1, 1]."""
        r = self.rng
        tk = ctx["ticker"]
        others = [t for t in ctx.get("tickers", [tk]) if t != tk] or [tk]
        chg = ctx["change_pct"]
        move = ("ripping" if chg > 6 else "pumping" if chg > 2 else "flat" if chg > -2 else "bleeding" if chg > -6 else "collapsing")
        fresh = [n for n in ctx["news"] if n["age"] < 40]
        for _ in range(5):
            pools = []
            n0 = fresh[0] if fresh else None
            doubter = n0 is not None and (side == "hold" or (mood_of(n0["text"]) > 0) == (side == "sell"))
            if doubter and n0.get("cred", 1.0) < 0.6 and r.random() < 0.7:  # only those trading against the leak scoff at it
                pools.append((SKEPTIC, {"a": fresh[0].get("author", "someone")}))
            elif fresh and r.random() < 0.55:
                n = fresh[0]
                s = mood_of(n["text"])
                mood = "good" if s > 0 else "bad" if s < 0 else "meh"
                key = (side, mood) if side != "hold" else None
                if key in REACT:
                    pools.append((REACT[key], {"n": _snip(n["text"])}))
            if not pools and ctx["timeline"] and side != "hold" and r.random() < 0.3:
                p = r.choice(ctx["timeline"])
                agree = (p["side"] > 0) == (side == "buy")
                tail = r.choice(VOICE[agent.archetype.key][side])
                pools.append(([r.choice(AGREE if agree else DISAGREE) + ". " + tail], {"h": p["handle"]}))
            if not pools:
                pools.append((VOICE[agent.archetype.key][side], {}))
            lines, extra = pools[0]
            text = r.choice(lines).format(t="$" + tk, co=ctx.get("company", tk), p=f"${ctx['price']:.2f}",
                                          chg=f"{chg:+.1f}%", move=move, other="$" + r.choice(others),
                                          **{"n": "", "h": "", "a": "", **extra})
            if agent.quirk and r.random() < 0.6:
                text = _quirk(text, agent.quirk, side, r)
            if text not in self._seen:
                break
        if len(self.recent) == self.recent.maxlen:
            self._seen.discard(self.recent[0])
        self.recent.append(text)
        self._seen.add(text)
        return text[:160]
