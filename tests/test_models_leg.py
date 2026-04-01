"""Tests for OrderLeg, LegGroup, LegGroupResult dataclasses."""
from flint.models import OrderLeg, LegGroup, LegGroupResult, Side


class TestOrderLeg:
    def test_create(self):
        leg = OrderLeg(order_id="ord-1", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0)
        assert leg.venue == "drift"
        assert leg.side == Side.LONG
        assert leg.size == 10.0

    def test_default_order_id(self):
        leg = OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0)
        assert leg.order_id == ""


class TestLegGroup:
    def test_create_with_two_legs(self):
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        group = LegGroup(group_id="grp-1", legs=legs, timeout_s=30.0)
        assert group.group_id == "grp-1"
        assert len(group.legs) == 2
        assert group.status == "pending"
        assert group.timeout_s == 30.0

    def test_default_status(self):
        group = LegGroup(group_id="grp-2", legs=[])
        assert group.status == "pending"
        assert group.created_at == 0


class TestLegGroupResult:
    def test_all_filled(self):
        result = LegGroupResult(
            group_id="grp-1", status="filled",
            filled_legs=["ord-1", "ord-2"], failed_legs=[], unwind_order_ids=[],
        )
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
        assert len(result.failed_legs) == 0

    def test_partial_with_unwind(self):
        result = LegGroupResult(
            group_id="grp-1", status="unwound",
            filled_legs=["ord-1"], failed_legs=["ord-2"],
            unwind_order_ids=["unwind-1"],
        )
        assert result.status == "unwound"
        assert len(result.unwind_order_ids) == 1

    def test_all_failed(self):
        result = LegGroupResult(
            group_id="grp-1", status="failed",
            filled_legs=[], failed_legs=["ord-1", "ord-2"], unwind_order_ids=[],
        )
        assert result.status == "failed"
