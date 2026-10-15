"""Technical analysis for Market Days charts: indicator series plus a plain-English buy/sell read.

Bars are dicts {"t": unix seconds, "o", "h", "l", "c", "v"}, oldest first. Every series is a list of
{"t", "v"} points so the browser can hand them straight to the chart. Nothing here is advice; the
"signal" is a teaching aid that explains what each classic indicator is saying and why.
"""

from __future__ import annotations

import math


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


def _series(bars: list[dict], values: list, skip: int = 0) -> list[dict]:
    return [{"t": b["t"], "v": round(v, 4)} for i, (b, v) in enumerate(zip(bars, values)) if v is not None and i >= skip]


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def signal(bars: list[dict]) -> dict:
    """Score -100..100 from five classic reads, each explained in a sentence a beginner can follow."""
    closes = [b["c"] for b in bars]
    reasons: list[dict] = []
    score = 0

    def add(points: int, text: str) -> None:
        nonlocal score
        score += points
        reasons.append({"text": text, "tone": "bull" if points > 0 else "bear" if points < 0 else "neutral"})

    if len(closes) < 15:
        return {"score": 0, "label": "NOT ENOUGH DATA", "tone": "neutral",
                "reasons": [{"text": "Need more price history before the indicators mean anything.", "tone": "neutral"}]}
    price = closes[-1]

    s20 = sma(closes, 20)[-1] if len(closes) >= 20 else None
    s50 = sma(closes, 50)[-1] if len(closes) >= 50 else None
    if s20 is not None:
        if price > s20:
            add(15, f"Price {_fmt(price)} is above its 20-bar average ({_fmt(s20)}): the short-term trend is up.")
        else:
            add(-15, f"Price {_fmt(price)} is below its 20-bar average ({_fmt(s20)}): the short-term trend is down.")
    if s20 is not None and s50 is not None:
        if s20 > s50:
            add(15, "The 20-bar average is above the 50-bar average: the bigger trend is up too.")
        else:
            add(-15, "The 20-bar average is below the 50-bar average: the bigger trend is down.")

    r = rsi(closes)[-1]
    if r is not None:
        if r < 30:
            add(25, f"RSI is {r:.0f} (under 30 = oversold): it has fallen hard and often bounces from here.")
        elif r > 70:
            add(-25, f"RSI is {r:.0f} (over 70 = overbought): it has run up fast and often pulls back.")
        else:
            add(0, f"RSI is {r:.0f}: neither overbought nor oversold.")

    line, sig, hist = macd(closes)
    if len(closes) >= 27:
        crossed_up = any(hist[i - 1] <= 0 < hist[i] for i in range(max(1, len(hist) - 3), len(hist)))
        crossed_down = any(hist[i - 1] >= 0 > hist[i] for i in range(max(1, len(hist) - 3), len(hist)))
        if crossed_up:
            add(25, "MACD just crossed above its signal line: momentum is turning up (a classic buy signal).")
        elif crossed_down:
            add(-25, "MACD just crossed below its signal line: momentum is turning down (a classic sell signal).")
        elif hist[-1] > 0:
            add(10, "MACD is above its signal line: momentum is still positive.")
        else:
            add(-10, "MACD is below its signal line: momentum is still negative.")

    upper, lower = bollinger(closes)
    if upper[-1] is not None:
        if price <= lower[-1]:
            add(20, "Price is at the bottom Bollinger band: it's unusually cheap versus its recent range.")
        elif price >= upper[-1]:
            add(-20, "Price is at the top Bollinger band: it's unusually expensive versus its recent range.")

    score = max(-100, min(100, score))
    label = ("STRONG BUY" if score >= 45 else "BUY" if score >= 15 else
             "STRONG SELL" if score <= -45 else "SELL" if score <= -15 else "HOLD")
    tone = "bull" if score >= 15 else "bear" if score <= -15 else "neutral"
    return {"score": score, "label": label, "tone": tone, "reasons": reasons}


def analyze(bars: list[dict], show: int | None = None) -> dict:
    """Indicators over all bars (older ones warm the averages up), trimmed to the last `show` bars for display."""
    closes = [b["c"] for b in bars]
    upper, lower = bollinger(closes)
    line, sig, hist = macd(closes)
    start = max(0, len(bars) - show) if show else 0
    cut = bars[start]["t"] if bars else 0
    trim = lambda series: [p for p in series if p["t"] >= cut]  # noqa: E731
    first, last = (closes[start], closes[-1]) if closes else (0, 0)
    return {
        "bars": bars[start:],
        "sma20": trim(_series(bars, sma(closes, 20))),
        "sma50": trim(_series(bars, sma(closes, 50))),
        "bb_upper": trim(_series(bars, upper)),
        "bb_lower": trim(_series(bars, lower)),
        "rsi": trim(_series(bars, rsi(closes))),
        "macd": trim(_series(bars, line, 25)),
        "macd_signal": trim(_series(bars, sig, 25)),
        "macd_hist": trim(_series(bars, hist, 25)),
        "change_pct": round((last / first - 1) * 100, 2) if first else 0.0,
        "signal": signal(bars),
    }
