"""Technical analysis for Market Days charts: indicator series plus a plain-English buy/sell read.

Bars are dicts {"t": unix seconds, "o", "h", "l", "c", "v"}, oldest first. Every series is a list of
{"t", "v"} points so the browser can hand them straight to the chart. Nothing here is advice; the
"signal" is a teaching aid that explains what each classic indicator is saying and why, and the
backtest shows honestly how following it would have worked out over the window on screen.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

BUY_AT, SELL_AT = 15, -15  # score thresholds for BUY / SELL (and the backtest's in/out rule)


def sma(values: list[float], n: int) -> list[float | None]:
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float]:
    k, out, e = 2 / (n + 1), [], None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    gain, loss = gains / n, losses / n
    for i in range(n, len(values)):
        if i > n:
            d = values[i] - values[i - 1]
            gain = (gain * (n - 1) + max(d, 0)) / n
            loss = (loss * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def bollinger(values: list[float], n: int = 20, k: float = 2.0) -> tuple[list, list]:
    mid, upper, lower = sma(values, n), [], []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - n + 1:i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        upper.append(m + k * sd)
        lower.append(m - k * sd)
    return upper, lower


def macd(values: list[float]) -> tuple[list[float], list[float], list[float]]:
    fast, slow = ema(values, 12), ema(values, 26)
    line = [f - s for f, s in zip(fast, slow)]
    signal = ema(line, 9)
    return line, signal, [m - s for m, s in zip(line, signal)]


def stochastic(bars: list[dict], n: int = 14, d: int = 3) -> tuple[list, list]:
    """%K (where the close sits in the last n bars' range, 0-100) and its d-bar average %D."""
    k: list[float | None] = []
    for i in range(len(bars)):
        if i < n - 1:
            k.append(None)
            continue
        window = bars[i - n + 1:i + 1]
        hi, lo = max(b["h"] for b in window), min(b["l"] for b in window)
        k.append(50.0 if hi == lo else (bars[i]["c"] - lo) / (hi - lo) * 100)
    dline: list[float | None] = []
    for i in range(len(k)):
        window = [x for x in k[max(0, i - d + 1):i + 1] if x is not None]
        dline.append(sum(window) / d if len(window) == d else None)
    return k, dline


def atr(bars: list[dict], n: int = 14) -> list[float | None]:
    """Average true range: the typical size of one bar's move."""
    out: list[float | None] = []
    trs: list[float] = []
    for i, b in enumerate(bars):
        prev = bars[i - 1]["c"] if i else b["c"]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev)))
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            out.append(sum(trs) / n)
        else:
            out.append((out[-1] * (n - 1) + trs[-1]) / n)
    return out


def vwap(bars: list[dict]) -> list[float]:
    """Volume-weighted average price, restarting each trading day (intraday charts only)."""
    out, day, pv, vol = [], None, 0.0, 0.0
    for b in bars:
        d = datetime.fromtimestamp(b["t"], timezone.utc).date()
        if d != day:
            day, pv, vol = d, 0.0, 0.0
        typical = (b["h"] + b["l"] + b["c"]) / 3
        pv += typical * b.get("v", 0)
        vol += b.get("v", 0)
        out.append(pv / vol if vol else typical)
    return out


def _series(bars: list[dict], values: list, skip: int = 0) -> list[dict]:
    return [{"t": b["t"], "v": round(v, 4)} for i, (b, v) in enumerate(zip(bars, values)) if v is not None and i >= skip]


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


class _Ind:
    """Every indicator computed once over the whole history so a score can be read at any bar cheaply."""

    def __init__(self, bars: list[dict]):
        self.bars = bars
        self.c = [b["c"] for b in bars]
        self.v = [b.get("v", 0.0) for b in bars]
        self.sma20, self.sma50 = sma(self.c, 20), sma(self.c, 50)
        self.rsi = rsi(self.c)
        self.macd, self.macd_sig, self.hist = macd(self.c)
        self.bb_u, self.bb_l = bollinger(self.c)
        self.k, self.d = stochastic(bars)
        self.atr = atr(bars)
        self.vol20 = sma(self.v, 20)

    def read(self, i: int) -> tuple[int, list[dict]]:
        """(score, tiles) at bar i: each tile is one indicator's verdict, with points toward the score."""
        c, tiles = self.c[i], []

        def tile(key, name, value, points, text, explain, tone=None):
            tiles.append({"key": key, "name": name, "value": value, "points": points, "text": text, "explain": explain,
                          "tone": tone or ("bull" if points > 0 else "bear" if points < 0 else "neutral")})

        s20, s50 = self.sma20[i], self.sma50[i]
        if s20 is not None:
            up = c > s20
            tile("trend", "Short trend", ("above" if up else "below") + f" {_fmt(s20)}", 15 if up else -15,
                 f"Price {_fmt(c)} is {'above' if up else 'below'} its 20-bar average ({_fmt(s20)}): "
                 f"the short-term trend is {'up' if up else 'down'}.",
                 "The 20-bar moving average smooths out noise. Price above it = uptrend, below = downtrend.")
        if s20 is not None and s50 is not None:
            up = s20 > s50
            tile("bigtrend", "Big trend", "20 " + (">" if up else "<") + " 50 avg", 15 if up else -15,
                 "The 20-bar average is above the 50-bar average: the bigger trend is up too." if up else
                 "The 20-bar average is below the 50-bar average: the bigger trend is down.",
                 "When the fast average is above the slow one, the longer trend is up (a 'golden cross' is when it "
                 "crosses above; a 'death cross' is when it crosses below).")
        r = self.rsi[i]
        if r is not None:
            if r < 30:
                pts, text = 25, f"RSI is {r:.0f} (under 30 = oversold): it has fallen hard and often bounces from here."
            elif r > 70:
                pts, text = -25, f"RSI is {r:.0f} (over 70 = overbought): it has run up fast and often pulls back."
            else:
                pts, text = 0, f"RSI is {r:.0f}: neither overbought nor oversold."
            tile("rsi", "RSI 14", f"{r:.0f}", pts, text,
                 "Relative Strength Index, 0-100: how hard recent moves pushed up vs down. Over 70 overbought, "
                 "under 30 oversold.")
        if i >= 26:
            h = self.hist
            recent = range(max(1, i - 2), i + 1)
            if any(h[j - 1] <= 0 < h[j] for j in recent):
                pts, val, text = 25, "bull cross", "MACD just crossed above its signal line: momentum is turning up (a classic buy signal)."
            elif any(h[j - 1] >= 0 > h[j] for j in recent):
                pts, val, text = -25, "bear cross", "MACD just crossed below its signal line: momentum is turning down (a classic sell signal)."
            elif h[i] > 0:
                pts, val, text = 10, "above signal", "MACD is above its signal line: momentum is still positive."
            else:
                pts, val, text = -10, "below signal", "MACD is below its signal line: momentum is still negative."
            tile("macd", "MACD", val, pts, text,
                 "Moving Average Convergence Divergence compares a fast and slow average. When the blue line "
                 "crosses above the orange one, momentum is turning up.")
        u, lo = self.bb_u[i], self.bb_l[i]
        if u is not None:
            pos = 50 if u == lo else (c - lo) / (u - lo) * 100
            if c <= lo:
                pts, text = 20, "Price is at the bottom Bollinger band: it's unusually cheap versus its recent range."
            elif c >= u:
                pts, text = -20, "Price is at the top Bollinger band: it's unusually expensive versus its recent range."
            else:
                pts, text = 0, f"Price is inside its Bollinger bands ({pos:.0f}% of the way from bottom to top): a normal range."
            tile("bb", "Bollinger", f"{pos:.0f}% of band", pts, text,
                 "Bands two standard deviations around the 20-bar average. About 95% of prices land inside, so "
                 "touching an edge is unusual.")
        k, d = self.k[i], self.d[i]
        if k is not None and d is not None and self.d[i - 1] is not None and self.k[i - 1] is not None:
            crossed_up = self.k[i - 1] <= self.d[i - 1] and k > d
            crossed_dn = self.k[i - 1] >= self.d[i - 1] and k < d
            if k < 20 and crossed_up:
                pts, text = 10, f"Stochastic is {k:.0f} and turning up from oversold: sellers are running out of steam."
            elif k > 80 and crossed_dn:
                pts, text = -10, f"Stochastic is {k:.0f} and turning down from overbought: buyers are running out of steam."
            elif k < 20:
                pts, text = 0, f"Stochastic is {k:.0f}: closing near the bottom of its recent range (oversold zone)."
            elif k > 80:
                pts, text = 0, f"Stochastic is {k:.0f}: closing near the top of its recent range (overbought zone)."
            else:
                pts, text = 0, f"Stochastic is {k:.0f}: mid-range."
            tile("stoch", "Stochastic", f"{k:.0f}", pts, text,
                 "Where today's close sits within the last 14 bars' high-low range (0 = at the low, 100 = at the high).")
        avg = self.vol20[i]
        if avg and self.v[i]:
            ratio = self.v[i] / avg
            green = c >= self.bars[i]["o"]
            if ratio >= 1.5:
                pts = 10 if green else -10
                text = (f"Volume is {ratio:.1f}x normal on an {'up' if green else 'down'} bar: "
                        f"{'buyers' if green else 'sellers'} mean it.")
            else:
                pts, text = 0, f"Volume is {ratio:.1f}x normal: nothing unusual."
            tile("volume", "Volume", f"{ratio:.1f}x avg", pts, text,
                 "How many shares traded versus the 20-bar average. Big moves on heavy volume are more believable.")
        a = self.atr[i]
        if a is not None and c:
            tile("atr", "Volatility", f"±{_fmt(a)} ({a / c * 100:.1f}%)", 0,
                 f"A typical bar moves about ${_fmt(a)} ({a / c * 100:.1f}%). Size your trade so that swing won't scare you.",
                 "Average True Range: the typical size of one bar's move. Higher = wilder.", tone="info")
        return max(-100, min(100, sum(t["points"] for t in tiles))), tiles


def _label(score: int) -> tuple[str, str]:
    label = ("STRONG BUY" if score >= 45 else "BUY" if score >= BUY_AT else
             "STRONG SELL" if score <= -45 else "SELL" if score <= SELL_AT else "HOLD")
    return label, "bull" if score >= BUY_AT else "bear" if score <= SELL_AT else "neutral"


def _signal_at(ind: _Ind, i: int) -> dict:
    score, tiles = ind.read(i)
    label, tone = _label(score)
    return {"score": score, "label": label, "tone": tone, "tiles": tiles,
            "reasons": [{"text": t["text"], "tone": t["tone"]} for t in tiles if t["tone"] != "info"]}


def signal(bars: list[dict]) -> dict:
    """Score -100..100 from classic reads, each explained in a sentence a beginner can follow."""
    if len(bars) < 15:
        return {"score": 0, "label": "NOT ENOUGH DATA", "tone": "neutral", "tiles": [],
                "reasons": [{"text": "Need more price history before the indicators mean anything.", "tone": "neutral"}]}
    return _signal_at(_Ind(bars), len(bars) - 1)


def backtest(ind: _Ind, start: int) -> dict:
    """Follow the signal over bars[start:]: own the stock after a BUY read, step aside after a SELL read."""
    c, long_, equity, flips = ind.c, False, 1.0, []
    for i in range(max(start, 15), len(c)):
        if i > start and long_:
            equity *= c[i] / c[i - 1]
        score, _ = ind.read(i)
        want = True if score >= BUY_AT else False if score <= SELL_AT else long_
        if want != long_ and i < len(c) - 1:
            flips.append({"t": ind.bars[i]["t"], "side": "buy" if want else "sell"})
        long_ = want
    hold = c[-1] / c[start] if c[start] else 1.0
    return {"strategy_pct": round((equity - 1) * 100, 2), "hold_pct": round((hold - 1) * 100, 2),
            "trades": len(flips), "flips": flips, "in_market": long_}


def analyze(bars: list[dict], show: int | None = None, intraday: bool = False) -> dict:
    """Indicators over all bars (older ones warm the averages up), trimmed to the last `show` bars for display."""
    ind = _Ind(bars)
    start = max(0, len(bars) - show) if show else 0
    cut = bars[start]["t"] if bars else 0
    trim = lambda series: [p for p in series if p["t"] >= cut]  # noqa: E731
    first, last = (ind.c[start], ind.c[-1]) if bars else (0, 0)
    shown = bars[start:]
    k, d = stochastic(bars)
    return {
        "bars": shown,
        "sma20": trim(_series(bars, ind.sma20)),
        "sma50": trim(_series(bars, ind.sma50)),
        "ema9": trim(_series(bars, ema(ind.c, 9), 8)),
        "ema21": trim(_series(bars, ema(ind.c, 21), 20)),
        "bb_upper": trim(_series(bars, ind.bb_u)),
        "bb_lower": trim(_series(bars, ind.bb_l)),
        "vwap": trim(_series(bars, vwap(bars))) if intraday else [],
        "rsi": trim(_series(bars, ind.rsi)),
        "macd": trim(_series(bars, ind.macd, 25)),
        "macd_signal": trim(_series(bars, ind.macd_sig, 25)),
        "macd_hist": trim(_series(bars, ind.hist, 25)),
        "stoch_k": trim(_series(bars, k)),
        "stoch_d": trim(_series(bars, d)),
        "levels": {"support": round(min(b["l"] for b in shown[-20:]), 2),
                   "resistance": round(max(b["h"] for b in shown[-20:]), 2)} if shown else None,
        "high": round(max(b["h"] for b in shown), 2) if shown else None,
        "low": round(min(b["l"] for b in shown), 2) if shown else None,
        "change_pct": round((last / first - 1) * 100, 2) if first else 0.0,
        "signal": _signal_at(ind, len(bars) - 1) if len(bars) >= 15 else signal(bars),
        "backtest": backtest(ind, start) if len(bars) >= 15 else None,
    }
