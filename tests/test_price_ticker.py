"""Tests for DLOB price ticker."""
import asyncio
from unittest.mock import patch

import pytest

from flint.paper.price_ticker import PriceTicker


def test_get_price_returns_none_initially():
    ticker = PriceTicker(["SOL-PERP"])
    assert ticker.get_price("SOL-PERP") is None


def test_get_price_after_update():
    ticker = PriceTicker(["SOL-PERP"])
    ticker.prices["SOL-PERP"] = 128.50
    assert ticker.get_price("SOL-PERP") == 128.50


def test_get_all_prices():
    ticker = PriceTicker(["SOL-PERP", "BTC-PERP"])
    ticker.prices["SOL-PERP"] = 128.50
    ticker.prices["BTC-PERP"] = 65000.0
    prices = ticker.get_all_prices()
    assert prices["SOL-PERP"] == 128.50
    assert prices["BTC-PERP"] == 65000.0


def test_add_market():
    ticker = PriceTicker(["SOL-PERP"])
    ticker.add_market("BTC-PERP")
    assert "BTC-PERP" in ticker.markets
    ticker.add_market("SOL-PERP")  # duplicate - should not add
    assert ticker.markets.count("SOL-PERP") == 1


@pytest.mark.asyncio
async def test_run_fetches_prices():
    ticker = PriceTicker(["SOL-PERP"], interval_s=0.1)
    with patch("flint.paper.price_ticker._fetch_mid_price", return_value=130.0):
        task = asyncio.create_task(ticker.run())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert ticker.get_price("SOL-PERP") == 130.0


def test_stop():
    ticker = PriceTicker(["SOL-PERP"])
    assert not ticker._running
    ticker._running = True
    ticker.stop()
    assert not ticker._running
