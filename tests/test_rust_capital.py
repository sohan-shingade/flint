"""Per-venue capital allocation and transfer parity tests."""
from __future__ import annotations


from flint.execution.capital import TransferConfig, VenueAllocator


class TestVenueAllocator:
    def test_initial_balances(self):
        alloc = VenueAllocator({"drift": 5000, "hyperliquid": 3000})
        assert alloc.total_cash == 8000
        assert alloc.available("drift") == 5000
        assert alloc.available("hyperliquid") == 3000

    def test_debit_success(self):
        alloc = VenueAllocator({"drift": 5000})
        assert alloc.debit("drift", 1000) is True
        assert alloc.available("drift") == 4000

    def test_debit_insufficient(self):
        alloc = VenueAllocator({"drift": 100})
        assert alloc.debit("drift", 200) is False
        assert alloc.available("drift") == 100

    def test_credit(self):
        alloc = VenueAllocator({"drift": 1000})
        alloc.credit("drift", 500)
        assert alloc.available("drift") == 1500

    def test_transfer_basic(self):
        alloc = VenueAllocator(
            {"drift": 5000, "hyperliquid": 3000},
            TransferConfig(default_time_s=1800, default_cost_usd=1.0))

        t = alloc.transfer("drift", "hyperliquid", 1000, current_ts=0)
        assert t is not None
        assert t.from_venue == "drift"
        assert t.to_venue == "hyperliquid"
        assert t.amount == 1000
        assert t.arrival_ts == 1800

        # Drift balance reduced immediately (amount + cost)
        assert alloc.available("drift") == 5000 - 1000 - 1.0
        # Hyperliquid not yet credited
        assert alloc.available("hyperliquid") == 3000

    def test_transfer_arrival(self):
        alloc = VenueAllocator(
            {"drift": 5000, "hyperliquid": 3000},
            TransferConfig(default_time_s=1800, default_cost_usd=0.0))

        alloc.transfer("drift", "hyperliquid", 1000, current_ts=0)

        # Before arrival
        arrived = alloc.process_arrivals(current_ts=1799)
        assert len(arrived) == 0
        assert alloc.available("hyperliquid") == 3000

        # At arrival
        arrived = alloc.process_arrivals(current_ts=1800)
        assert len(arrived) == 1
        assert alloc.available("hyperliquid") == 4000

    def test_transfer_insufficient_funds(self):
        alloc = VenueAllocator({"drift": 100})
        t = alloc.transfer("drift", "hyperliquid", 200, current_ts=0)
        assert t is None
        assert alloc.available("drift") == 100

    def test_total_cash_includes_transit(self):
        alloc = VenueAllocator(
            {"drift": 5000, "hyperliquid": 3000},
            TransferConfig(default_time_s=1800, default_cost_usd=0.0))
        alloc.transfer("drift", "hyperliquid", 1000, current_ts=0)
        # Total should still be 8000 (in-transit counts)
        assert alloc.total_cash == 8000

    def test_pnl_tracking(self):
        alloc = VenueAllocator({"drift": 5000, "hyperliquid": 3000})
        alloc.track_pnl("drift", 200)
        alloc.track_pnl("hyperliquid", -50)

        metrics = alloc.compute_metrics(final_equity=8150)
        assert metrics.venue_pnl["drift"] == 200
        assert metrics.venue_pnl["hyperliquid"] == -50

    def test_multiple_venues(self):
        """Test with all common venues."""
        balances = {
            "drift": 2000,
            "hyperliquid": 2000,
            "binance": 2000,
            "okx": 1000,
            "bybit": 1000,
            "coinbase": 1000,
            "kraken": 500,
        }
        alloc = VenueAllocator(balances)
        assert alloc.total_cash == 9500

        # Debit from each
        for venue in balances:
            assert alloc.debit(venue, 100) is True

        assert alloc.total_cash == 9500 - 700

    def test_unknown_venue_debit(self):
        alloc = VenueAllocator({"drift": 5000})
        assert alloc.debit("unknown", 100) is False

    def test_fragmentation_metrics(self):
        alloc = VenueAllocator({"drift": 5000, "hyperliquid": 3000})
        alloc.debit("drift", 2000)
        alloc.credit("drift", 2500)
        alloc.track_pnl("drift", 500)

        metrics = alloc.compute_metrics(final_equity=8500)
        assert metrics.total_deployed == 8000
        assert metrics.venue_pnl["drift"] == 500
