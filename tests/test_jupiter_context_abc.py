import inspect
from flint.execution.context import ExecutionContext


def test_get_borrow_rate_default_returns_none():
    assert hasattr(ExecutionContext, "get_borrow_rate")
    assert hasattr(ExecutionContext, "get_borrow_rates")

def test_get_borrow_rate_method_signature():
    sig = inspect.signature(ExecutionContext.get_borrow_rate)
    params = list(sig.parameters.keys())
    assert "market" in params
    assert "venue" in params

def test_get_borrow_rates_method_signature():
    sig = inspect.signature(ExecutionContext.get_borrow_rates)
    params = list(sig.parameters.keys())
    assert "market" in params
    assert "venue" in params
    assert "lookback" in params
