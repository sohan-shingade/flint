"""Tests for dynamic strategy loader with AST validation."""
import pytest
from flint.strategy.loader import load_user_strategy, validate_strategy_code, StrategyLoadError


VALID_STRATEGY = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < 2:
            return Signal.HOLD
        if candle.close > history[-2].close:
            return Signal.BUY
        return Signal.SELL

    def reset(self) -> None:
        pass
'''

MISSING_ON_CANDLE = '''
from flint.strategy.base import Strategy
from flint.models import Signal

class BadStrategy(Strategy):
    @property
    def name(self) -> str:
        return "bad"

    def reset(self) -> None:
        pass
'''

SYNTAX_ERROR_CODE = '''
def this is broken(
'''

NO_STRATEGY_CLASS = '''
x = 42
def foo():
    return x
'''

SUSPICIOUS_IMPORT = '''
import os
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class OsStrategy(Strategy):
    @property
    def name(self) -> str:
        return "os_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''


def test_load_valid_strategy():
    strategy = load_user_strategy(VALID_STRATEGY)
    assert strategy.name == "my_strategy"


def test_validate_valid_code():
    result = validate_strategy_code(VALID_STRATEGY)
    assert result["valid"] is True
    assert result["warnings"] == []


def test_validate_missing_method():
    result = validate_strategy_code(MISSING_ON_CANDLE)
    assert result["valid"] is False
    assert "on_candle" in result["error"]


def test_validate_syntax_error():
    result = validate_strategy_code(SYNTAX_ERROR_CODE)
    assert result["valid"] is False
    assert "line" in result["error"].lower() or "syntax" in result["error"].lower()


def test_validate_no_strategy_class():
    result = validate_strategy_code(NO_STRATEGY_CLASS)
    assert result["valid"] is False
    assert "Strategy" in result["error"]


def test_validate_suspicious_import_warns():
    result = validate_strategy_code(SUSPICIOUS_IMPORT)
    assert result["valid"] is True
    assert len(result["warnings"]) > 0
    assert "os" in result["warnings"][0]


def test_load_strategy_with_params():
    code = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class ParamStrategy(Strategy):
    def __init__(self, threshold=5.0):
        self.threshold = threshold

    @property
    def name(self) -> str:
        return "param_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''
    strategy = load_user_strategy(code, params={"threshold": 10.0})
    assert strategy.threshold == 10.0
