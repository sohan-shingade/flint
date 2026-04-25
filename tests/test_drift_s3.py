"""Tests for the Drift S3 provider."""
from __future__ import annotations

import gzip
from unittest.mock import MagicMock

import httpx
import pytest

from flint.providers.drift_s3 import DriftS3Provider, _date_range


# -- helper -------------------------------------------------------------------

_SAMPLE_CSV = """\
fillerReward,baseAssetAmountFilled,quoteAssetAmountFilled,takerFee,makerRebate,referrerReward,quoteAssetAmountSurplus,takerOrderBaseAssetAmount,takerOrderCumulativeBaseAssetAmountFilled,takerOrderCumulativeQuoteAssetAmountFilled,makerOrderBaseAssetAmount,makerOrderCumulativeBaseAssetAmountFilled,makerOrderCumulativeQuoteAssetAmountFilled,oraclePrice,makerFee,txSig,slot,ts,action,actionExplanation,marketIndex,marketType,filler,fillRecordId,taker,takerOrderId,takerOrderDirection,maker,makerOrderId,makerOrderDirection,spotFulfillmentMethodFee,programId
0.0,1.0,100.0,0.005,0.0,0.0,0.0,1.0,1.0,100.0,0.0,0.0,0.0,100.0,0.0,sig1,1000,1700000100,fill,orderFilledWithAmm,0,perp,filler1,1,taker1,1,long,,,,0.0,prog1
0.0,2.0,210.0,0.01,0.0,0.0,0.0,2.0,2.0,210.0,0.0,0.0,0.0,105.0,0.0,sig2,1001,1700000200,fill,orderFilledWithAmm,0,perp,filler1,2,taker1,2,long,,,,0.0,prog1
0.0,0.5,55.0,0.002,0.0,0.0,0.0,0.5,0.5,55.0,0.0,0.0,0.0,110.0,0.0,sig3,1002,1700000300,fill,orderFilledWithAmm,0,perp,filler1,3,taker2,1,short,,,,0.0,prog1
0.0,1.5,160.0,0.008,0.0,0.0,0.0,1.5,1.5,160.0,0.0,0.0,0.0,107.0,0.0,sig4,1003,1700003700,fill,orderFilledWithAmm,0,perp,filler1,4,taker1,3,long,,,,0.0,prog1
"""


def _make_mock_client(csv_text: str) -> httpx.Client:
    """Return a mock httpx.Client whose .get() returns gzipped csv_text."""
    compressed = gzip.compress(csv_text.encode())
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.content = compressed
    mock_resp.raise_for_status = MagicMock()

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp
    return client


# -- tests --------------------------------------------------------------------


def test_date_range():
    dates = _date_range(1700000000, 1700172800)  # ~2 days
    assert dates[0] == "20231114"
    assert len(dates) >= 2


def test_aggregate_trades_single_bucket():
    client = _make_mock_client(_SAMPLE_CSV)
    provider = DriftS3Provider(client=client)

    candles = provider.fetch_candles(
        market="SOL-PERP",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700003700,
    )

    # ts 1700000100,200,300 → bucket (1700000100//3600)*3600 = 1699999200
    # ts 1700003700 → bucket (1700003700//3600)*3600 = 1700003600
    assert len(candles) == 2

    first = candles[0]
    assert first.ts == 1699999200
    assert first.open == 100.0
    assert first.high == 110.0
    assert first.low == 100.0
    assert first.close == 110.0
    assert first.volume == pytest.approx(3.5)
    assert first.market == "SOL-PERP"
    assert first.resolution_s == 3600

    second = candles[1]
    assert second.ts == 1700002800  # (1700003700 // 3600) * 3600
    assert second.open == 107.0
    assert second.close == 107.0
    assert second.volume == pytest.approx(1.5)


def test_404_returns_empty():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = mock_resp

    provider = DriftS3Provider(client=client)
    candles = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700003600)
    assert candles == []


def test_time_filtering():
    client = _make_mock_client(_SAMPLE_CSV)
    provider = DriftS3Provider(client=client)

    # only include trades in the first bucket
    candles = provider.fetch_candles(
        market="SOL-PERP",
        resolution_s=3600,
        start_ts=1700000000,
        end_ts=1700000300,
    )
    assert len(candles) == 1
    assert candles[0].volume == pytest.approx(3.5)
