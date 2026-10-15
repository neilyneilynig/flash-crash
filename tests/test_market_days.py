import asyncio

import httpx
import pytest

from flashcrash.market_days import AlpacaPaper, AlpacaSettings, MarketDaysError


def test_alpaca_paper_quote_and_order():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/quotes/latest"):
            return httpx.Response(200, json={"quote": {"bp": 100.1, "ap": 100.2, "t": "now"}})
        return httpx.Response(200, json={"id": "paper-order"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        alpaca = AlpacaPaper(AlpacaSettings("key", "secret", "https://trade", "https://data"), client)
        return await alpaca.quote("SPY"), await alpaca.paper_order("SPY", "buy")

    quote, order = asyncio.run(run())
    assert quote == {"symbol": "SPY", "bid": 100.1, "ask": 100.2, "at": "now"}
    assert order["id"] == "paper-order"
    assert requests[0].headers["APCA-API-KEY-ID"] == "key"


def test_alpaca_paper_rejects_invalid_order():
    alpaca = AlpacaPaper(AlpacaSettings("key", "secret", "https://trade", "https://data"),
                         httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    with pytest.raises(MarketDaysError, match="invalid"):
        asyncio.run(alpaca.paper_order("SPY", "hold"))
