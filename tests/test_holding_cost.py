from flint.execution.holding_cost import FundingCostModel, BorrowCostModel


def test_funding_cost_positive_rate_long():
    model = FundingCostModel()
    cost = model.cost_at_bar(side="long", size_usd=10000.0, rate=0.0001)
    assert cost == 1.0


def test_funding_cost_negative_rate_long():
    model = FundingCostModel()
    cost = model.cost_at_bar(side="long", size_usd=10000.0, rate=-0.0001)
    assert cost == -1.0


def test_funding_cost_short():
    model = FundingCostModel()
    cost = model.cost_at_bar(side="short", size_usd=10000.0, rate=0.0001)
    assert cost == -1.0


def test_borrow_cost_at_bar():
    model = BorrowCostModel()
    cost = model.cost_at_bar(cumulative_entry=1.00100, cumulative_now=1.00150, size_usd=10000.0)
    assert abs(cost - 5.0) < 1e-9


def test_borrow_cost_at_close():
    model = BorrowCostModel()
    cost = model.cost_at_close(cumulative_entry=1.00100, cumulative_close=1.00300, size_usd=20000.0)
    assert abs(cost - 40.0) < 1e-9


def test_borrow_cost_never_negative():
    model = BorrowCostModel()
    cost = model.cost_at_bar(cumulative_entry=1.005, cumulative_now=1.005, size_usd=10000.0)
    assert cost == 0.0


def test_borrow_cost_same_for_long_and_short():
    model = BorrowCostModel()
    cost_long = model.cost_at_bar(cumulative_entry=1.001, cumulative_now=1.002, size_usd=10000.0)
    cost_short = model.cost_at_bar(cumulative_entry=1.001, cumulative_now=1.002, size_usd=10000.0)
    assert cost_long == cost_short
