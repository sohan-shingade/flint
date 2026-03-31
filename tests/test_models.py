"""Tests for data models."""
from flint.models import (
    Candle, Position, Signal, Side, BacktestResult,
    OpenInterest, Liquidation, WhaleTransfer, DexVolume, TokenUnlock, SyncMetadata,
    OrderState,
)


def test_candle_frozen():
    c = Candle(ts=1, open=1, high=2, low=0.5, close=1.5, volume=10, market="X", resolution_s=60)
    assert c.ts == 1
    assert c.market == "X"
    # frozen — cannot assign
    try:
        c.ts = 2  # type: ignore[misc]
        assert False, "Should raise"
    except AttributeError:
        pass


def test_signal_values():
    assert Signal.BUY.value == "buy"
    assert Signal.SELL.value == "sell"
    assert Signal.HOLD.value == "hold"


def test_position_long():
    p = Position(entry_price=100, size=1.0, entry_ts=1000)
    assert p.side == Side.LONG
    assert not p.closed
    pnl = p.close(110, 2000)
    assert pnl == 10.0
    assert p.closed
    assert p.exit_price == 110
    assert p.exit_ts == 2000


def test_position_short():
    p = Position(entry_price=100, size=-1.0, entry_ts=1000)
    assert p.side == Side.SHORT
    pnl = p.close(90, 2000)
    assert pnl == 10.0  # (90-100)*(-1) = 10


def test_backtest_result_defaults():
    r = BacktestResult(
        total_pnl=100,
        win_rate=0.6,
        max_drawdown=0.05,
        sharpe_ratio=1.2,
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
    )
    assert r.positions == []
    assert r.equity_curve == []


# ---------------------------------------------------------------------------
# New data-provider models
# ---------------------------------------------------------------------------

def test_open_interest_creation():
    oi = OpenInterest(market="SOL-PERP", ts=1700000000, long_oi=5000.0, short_oi=4500.0)
    assert oi.market == "SOL-PERP"
    assert oi.long_oi == 5000.0
    assert oi.net_oi == 500.0
    assert oi.total_oi == 9500.0


def test_open_interest_frozen():
    oi = OpenInterest(market="SOL-PERP", ts=1700000000, long_oi=5000.0, short_oi=4500.0)
    try:
        oi.market = "BTC-PERP"  # type: ignore[misc]
        assert False, "Should raise"
    except AttributeError:
        pass


def test_liquidation_creation():
    liq = Liquidation(market="SOL-PERP", ts=1700000000, side="long", size=10.0, price=150.0, slot=12345)
    assert liq.side == "long"
    assert liq.size == 10.0
    assert liq.price == 150.0
    assert liq.slot == 12345
    assert liq.tx_sig == ""


def test_liquidation_defaults():
    liq = Liquidation(market="SOL-PERP", ts=1700000000, side="short", size=5.0, price=120.0)
    assert liq.slot == 0
    assert liq.tx_sig == ""


def test_whale_transfer_creation():
    wt = WhaleTransfer(wallet="ABC123", token_mint="So111...", amount=50000.0, ts=1700000000, direction="in", tx_sig="xyz")
    assert wt.direction == "in"
    assert wt.wallet == "ABC123"
    assert wt.tx_sig == "xyz"


def test_whale_transfer_defaults():
    wt = WhaleTransfer(wallet="ABC123", token_mint="So111...", amount=50000.0, ts=1700000000, direction="out")
    assert wt.tx_sig == ""


def test_dex_volume_creation():
    dv = DexVolume(market="SOL/USDC", dex="raydium", ts=1700000000, volume_usd=1_000_000.0, txn_count=500)
    assert dv.dex == "raydium"
    assert dv.volume_usd == 1_000_000.0
    assert dv.txn_count == 500


def test_dex_volume_defaults():
    dv = DexVolume(market="SOL/USDC", dex="orca", ts=1700000000, volume_usd=500_000.0)
    assert dv.txn_count == 0


def test_token_unlock_creation():
    tu = TokenUnlock(token_mint="JUP...", unlock_ts=1710000000, amount=1_000_000.0, vesting_account="vest123")
    assert tu.token_mint == "JUP..."
    assert tu.unlock_ts == 1710000000
    assert tu.amount == 1_000_000.0
    assert tu.vesting_account == "vest123"


def test_token_unlock_defaults():
    tu = TokenUnlock(token_mint="JUP...", unlock_ts=1710000000, amount=1_000_000.0)
    assert tu.vesting_account == ""


def test_sync_metadata_creation():
    sm = SyncMetadata(provider="birdeye", market="SOL", data_type="candles", last_sync_ts=1700000000, record_count=5000)
    assert sm.provider == "birdeye"
    assert sm.record_count == 5000
    assert sm.status == "ok"
    assert sm.error_msg == ""


def test_sync_metadata_error_state():
    sm = SyncMetadata(
        provider="helius", market="SOL", data_type="whale_transfers",
        last_sync_ts=1700000000, record_count=0, status="error", error_msg="rate limited",
    )
    assert sm.status == "error"
    assert sm.error_msg == "rate limited"


# ---------------------------------------------------------------------------
# OrderState enum (live order lifecycle)
# ---------------------------------------------------------------------------

class TestOrderState:
    def test_all_states_exist(self):
        assert OrderState.PENDING.value == "pending"
        assert OrderState.SUBMITTED.value == "submitted"
        assert OrderState.CONFIRMED.value == "confirmed"
        assert OrderState.FILLED.value == "filled"
        assert OrderState.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderState.CANCELLED.value == "cancelled"
        assert OrderState.EXPIRED.value == "expired"
        assert OrderState.FAILED.value == "failed"

    def test_terminal_states(self):
        terminal = {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.FAILED}
        assert OrderState.PENDING not in terminal
        assert OrderState.SUBMITTED not in terminal
        assert OrderState.CONFIRMED not in terminal
        assert OrderState.FILLED in terminal
