"""D-2.1.b Step 5 — FundingLedger unit tests."""
from __future__ import annotations


from flint.execution.funding_ledger import FundingLedger
from flint.models import FundingRate


def _fr(market="SOL-PERP", venue="drift", ts=1700000000, rate=0.0001):
    return FundingRate(market=market, ts=ts, rate=rate, oracle_price=150.0,
                       mark_price=150.1, slot=0, source=venue)


class TestAddAndLatest:
    def test_empty_returns_none(self):
        fl = FundingLedger()
        assert fl.latest("SOL-PERP") is None

    def test_latest_returns_most_recent_rate(self):
        fl = FundingLedger()
        fl.add(_fr(rate=0.0001))
        fl.add(_fr(rate=0.0002, ts=1700003600))
        assert fl.latest("SOL-PERP") == 0.0002


class TestRecent:
    def test_returns_ts_rate_pairs(self):
        fl = FundingLedger()
        fl.add(_fr(ts=1, rate=0.001))
        fl.add(_fr(ts=2, rate=0.002))
        assert fl.recent("SOL-PERP") == [(1, 0.001), (2, 0.002)]

    def test_lookback_truncates(self):
        fl = FundingLedger()
        for i in range(50):
            fl.add(_fr(ts=i, rate=0.0001 * i))
        rec = fl.recent("SOL-PERP", lookback=5)
        assert len(rec) == 5
        assert rec[0][0] == 45


class TestByVenue:
    def test_groups_by_source(self):
        fl = FundingLedger()
        fl.add(_fr(venue="drift", ts=1, rate=0.0001))
        fl.add(_fr(venue="hyperliquid", ts=1, rate=0.0002))
        fl.add(_fr(venue="drift", ts=2, rate=0.0003))
        out = fl.by_venue("SOL-PERP")
        assert sorted(out.keys()) == ["drift", "hyperliquid"]
        assert out["drift"] == [(1, 0.0001), (2, 0.0003)]
        assert out["hyperliquid"] == [(1, 0.0002)]

    def test_empty_market_returns_empty_dict(self):
        fl = FundingLedger()
        assert fl.by_venue("BTC-PERP") == {}


class TestVenueSnapshots:
    def test_returns_full_objects(self):
        fl = FundingLedger()
        rate = _fr(venue="drift", ts=42, rate=0.0005)
        fl.add(rate)
        snaps = fl.venue_snapshots("SOL-PERP")
        assert "drift" in snaps
        assert snaps["drift"][0] is rate


class TestBacktestContextIntegration:
    def test_context_owns_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._fl, FundingLedger)

    def test_add_funding_rate_routes_through_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_funding_rate(_fr(rate=0.001))
        assert ctx._fl.latest("SOL-PERP") == 0.001

    def test_get_funding_rate_reads_from_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_funding_rate(_fr(rate=0.0007))
        assert ctx.get_funding_rate("SOL-PERP") == 0.0007

    def test_get_funding_by_venue_reads_from_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_funding_rate(_fr(venue="drift", ts=1, rate=0.0001))
        ctx.add_funding_rate(_fr(venue="hyperliquid", ts=1, rate=0.0003))
        out = ctx.get_funding_by_venue("SOL-PERP")
        assert sorted(out.keys()) == ["drift", "hyperliquid"]

    def test_legacy_funding_history_alias(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_funding_rate(_fr(rate=0.0009))
        # Old code paths peeked into self._funding_history directly.
        assert "SOL-PERP" in ctx._funding_history
        assert ctx._funding_history["SOL-PERP"][0].rate == 0.0009


class TestLedgerIsolation:
    def test_two_contexts_have_independent_ledgers(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000)
        b = BacktestContext(initial_capital=2_000)
        a.add_funding_rate(_fr(rate=0.0001))
        assert b.get_funding_rate("SOL-PERP") is None
        assert a._fl is not b._fl
