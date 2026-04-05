from flint.execution.jupiter_costs import JupiterTxCostModel, JupiterCostEstimate


def test_open_close_fees():
    model = JupiterTxCostModel()
    estimate = model.estimate_round_trip(size_usd=10000.0, hold_hours=24, rate_hourly=0.00008)
    assert estimate.open_fee == 6.0
    assert estimate.close_fee == 6.0

def test_borrow_cost_estimate():
    model = JupiterTxCostModel()
    cost = model.estimate_borrow(size_usd=10000.0, hours=24, rate_hourly=0.00008)
    assert abs(cost - 19.2) < 0.01

def test_round_trip_total():
    model = JupiterTxCostModel()
    estimate = model.estimate_round_trip(size_usd=10000.0, hold_hours=24, rate_hourly=0.00008)
    expected_total = 6.0 + 6.0 + 19.2
    assert abs(estimate.total - expected_total) < 0.1

def test_price_impact_scales_with_size():
    model = JupiterTxCostModel()
    small = model.estimate_round_trip(size_usd=1000.0, hold_hours=1, rate_hourly=0.00008)
    large = model.estimate_round_trip(size_usd=100000.0, hold_hours=1, rate_hourly=0.00008)
    assert large.price_impact > small.price_impact

def test_cost_estimate_dataclass():
    est = JupiterCostEstimate(open_fee=6.0, close_fee=6.0, price_impact=0.5, borrow_cost=19.2, total=31.7)
    assert est.open_fee == 6.0
    assert est.total == 31.7
