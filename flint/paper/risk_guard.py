"""Risk guard for paper trading — checks limits after each candle.

Supports:
- Max drawdown limit
- Daily loss limit
- Max position size as % of equity
- Perp liquidation simulation (Drift-like 5% maintenance margin)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("flint.paper")

MAINTENANCE_MARGIN_RATIO = 0.05
LIQUIDATION_FEE_PCT = 0.005


@dataclass
class RiskConfig:
    max_drawdown_pct: float = 0.15
    daily_loss_limit: float = 500.0
    max_position_pct: float = 0.95
    liquidation_enabled: bool = True


class RiskGuard:
    """Checks risk limits after each candle."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self._day_start_equity: float = 0.0
        self._current_day: int = 0

    def check(self, broker, initial_capital: float, mark_prices: dict) -> Optional[str]:
        """Returns breach reason string, or None if all limits OK."""
        # Max drawdown
        peak = max(broker.equity_history) if broker.equity_history else initial_capital
        if peak > 0:
            dd = (peak - broker.equity) / peak
            if dd > self.config.max_drawdown_pct:
                return f"max_drawdown ({dd:.1%} > {self.config.max_drawdown_pct:.1%})"

        # Daily loss
        utc_day = int(time.time()) // 86400
        if utc_day != self._current_day:
            self._day_start_equity = broker.equity
            self._current_day = utc_day

        if self._day_start_equity > 0:
            daily_loss = self._day_start_equity - broker.equity
            if daily_loss > self.config.daily_loss_limit:
                return f"daily_loss (${daily_loss:.0f} > ${self.config.daily_loss_limit:.0f})"

        # Position size
        if broker.equity > 0:
            for market, pos in broker.positions.items():
                mark = mark_prices.get(market, pos.get("entry_price", 0))
                pos_value = pos["size"] * mark
                pos_pct = pos_value / broker.equity
                if pos_pct > self.config.max_position_pct:
                    return f"max_position ({pos_pct:.0%} > {self.config.max_position_pct:.0%})"

        # Liquidation
        if self.config.liquidation_enabled:
            liq = self._check_liquidation(broker, mark_prices)
            if liq:
                return liq

        return None

    def _get_venue_margins(self, broker):
        """Extract margin params from broker's venue config, or use defaults."""
        mmr = MAINTENANCE_MARGIN_RATIO
        liq_fee = LIQUIDATION_FEE_PCT
        vc = getattr(broker, '_venue_config', None)
        if vc is not None and isinstance(getattr(vc, 'maintenance_margin', None), (int, float)):
            mmr = vc.maintenance_margin
            liq_fee = getattr(vc, 'liquidation_penalty', LIQUIDATION_FEE_PCT)
        return mmr, liq_fee

    def _check_liquidation(self, broker, mark_prices: dict) -> Optional[str]:
        # Use broker's venue config if available, otherwise fall back to defaults
        mmr, liq_fee = self._get_venue_margins(broker)

        total_margin_required = 0.0
        for market, pos in broker.positions.items():
            mark = mark_prices.get(market, pos.get("entry_price", 0))
            notional = pos["size"] * mark
            total_margin_required += notional * mmr

        if total_margin_required > 0 and broker.equity <= total_margin_required:
            for market, pos in list(broker.positions.items()):
                mark = mark_prices.get(market, pos.get("entry_price", 0))
                penalty = pos["size"] * mark * liq_fee
                broker.cash -= penalty
            if hasattr(broker, "close_all_positions"):
                broker.close_all_positions(mark_prices)
            return f"liquidation (equity ${broker.equity:.0f} < margin req ${total_margin_required:.0f})"
        return None

    def margin_ratio(self, broker, mark_prices: dict) -> float:
        mmr, _ = self._get_venue_margins(broker)
        total_req = 0.0
        for market, pos in broker.positions.items():
            mark = mark_prices.get(market, pos.get("entry_price", 0))
            total_req += pos["size"] * mark * mmr
        return broker.equity / total_req if total_req > 0 else float("inf")

    def liquidation_distance_pct(self, broker, mark_prices: dict) -> float:
        ratio = self.margin_ratio(broker, mark_prices)
        if ratio == float("inf"):
            return 1.0
        return max(0, 1 - 1 / ratio) if ratio > 0 else 0.0

    def risk_status(self, broker, initial_capital: float, mark_prices: dict) -> dict:
        peak = max(broker.equity_history) if broker.equity_history else initial_capital
        dd = (peak - broker.equity) / peak if peak > 0 else 0
        daily_loss = 0.0
        if self._day_start_equity > 0:
            daily_loss = self._day_start_equity - broker.equity
        max_pos_used = 0.0
        if broker.equity > 0:
            for market, pos in broker.positions.items():
                mark = mark_prices.get(market, pos.get("entry_price", 0))
                pos_pct = (pos["size"] * mark) / broker.equity
                max_pos_used = max(max_pos_used, pos_pct)
        return {
            "current_drawdown": round(dd, 4),
            "daily_loss": round(daily_loss, 2),
            "max_position_used": round(max_pos_used, 4),
            "margin_ratio": round(self.margin_ratio(broker, mark_prices), 4),
            "liquidation_distance_pct": round(self.liquidation_distance_pct(broker, mark_prices), 4),
            "any_breached": self.check(broker, initial_capital, mark_prices) is not None,
        }
