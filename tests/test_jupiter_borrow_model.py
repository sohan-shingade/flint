from flint.models import BorrowSnapshot


def test_borrow_snapshot_creation():
    bs = BorrowSnapshot(
        market="SOL-PERP",
        ts=1700000000,
        rate_hourly=0.00008,
        utilization=0.65,
        cumulative_rate=1.00234,
        source="rpc",
    )
    assert bs.market == "SOL-PERP"
    assert bs.rate_hourly == 0.00008
    assert bs.utilization == 0.65
    assert bs.cumulative_rate == 1.00234
    assert bs.source == "rpc"


def test_borrow_snapshot_frozen():
    bs = BorrowSnapshot(
        market="SOL-PERP", ts=1700000000, rate_hourly=0.00008,
        utilization=0.65, cumulative_rate=1.00234,
    )
    try:
        bs.market = "ETH-PERP"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_borrow_snapshot_defaults():
    bs = BorrowSnapshot(
        market="ETH-PERP", ts=1700000000, rate_hourly=0.0001,
        utilization=0.5, cumulative_rate=1.001,
    )
    assert bs.source == "rpc"
