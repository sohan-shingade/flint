//! Position state machine — open, close, DCA, flip, partial close.

use std::collections::HashMap;
use crate::types::*;

/// Position key: (venue_id, market_id).
pub type PosKey = (VenueId, MarketId);

/// Manages open positions and tracks closed trades.
pub struct PositionManager {
    pub positions: HashMap<PosKey, PositionState>,
    pub closed_trades: Vec<ClosedTrade>,
    cash: f64,
    total_fees: f64,
    total_tx_costs: f64,
    total_borrow_paid: f64,
    log_messages: Vec<String>,
}

impl PositionManager {
    pub fn new(initial_capital: f64) -> Self {
        Self {
            positions: HashMap::new(),
            closed_trades: Vec::new(),
            cash: initial_capital,
            total_fees: 0.0,
            total_tx_costs: 0.0,
            total_borrow_paid: 0.0,
            log_messages: Vec::new(),
        }
    }

    pub fn cash(&self) -> f64 { self.cash }
    pub fn total_fees(&self) -> f64 { self.total_fees }
    pub fn total_tx_costs(&self) -> f64 { self.total_tx_costs }
    pub fn total_borrow_paid(&self) -> f64 { self.total_borrow_paid }
    pub fn log_messages(&self) -> &[String] { &self.log_messages }

    pub fn equity(&self) -> f64 {
        let unrealized: f64 = self.positions.values().map(|p| p.unrealized_pnl).sum();
        self.cash + unrealized
    }

    pub fn update_pnl_for_market(&mut self, market_id: MarketId, price: f64) {
        for ((_, mid), pos) in self.positions.iter_mut() {
            if *mid == market_id {
                pos.update_pnl(price);
            }
        }
    }

    pub fn apply_fill(&mut self, fill: &FillResult) {
        // Deduct fee
        self.cash -= fill.fee;
        self.total_fees += fill.fee;

        if fill.tx_cost > 0.0 {
            self.cash -= fill.tx_cost;
            self.total_tx_costs += fill.tx_cost;
        }

        let key = (fill.venue_id, fill.market_id);
        let pos = self.positions.get(&key).cloned();

        match pos {
            None => {
                // Open new position
                self.positions.insert(key, PositionState {
                    market_id: fill.market_id,
                    venue_id: fill.venue_id,
                    side: fill.side,
                    size: fill.size,
                    entry_price: fill.price,
                    entry_ts: fill.ts,
                    unrealized_pnl: 0.0,
                    funding_paid: 0.0,
                    borrow_cumulative_at_entry: 0.0,
                });
            }
            Some(ref p) if p.side == fill.side => {
                // DCA — add to existing position
                let pos_mut = self.positions.get_mut(&key).expect("position must exist after match");
                let total_cost = pos_mut.entry_price * pos_mut.size + fill.price * fill.size;
                pos_mut.size += fill.size;
                pos_mut.entry_price = if pos_mut.size > 0.0001 {
                    total_cost / pos_mut.size
                } else {
                    fill.price
                };
            }
            Some(ref p) => {
                // Reducing or closing
                if fill.size >= p.size {
                    // Full close
                    let pnl = match p.side {
                        Side::Long => (fill.price - p.entry_price) * p.size,
                        Side::Short => (p.entry_price - fill.price) * p.size,
                    };
                    let pnl = (pnl * 1e8).round() / 1e8;

                    self.cash += pnl;
                    self.closed_trades.push(ClosedTrade {
                        market_id: fill.market_id,
                        venue_id: fill.venue_id,
                        side: p.side,
                        size: p.size,
                        entry_price: p.entry_price,
                        exit_price: fill.price,
                        entry_ts: p.entry_ts,
                        exit_ts: fill.ts,
                        pnl,
                        funding_paid: p.funding_paid,
                        borrow_paid: 0.0,
                        liquidated: false,
                        partial: false,
                    });

                    let remainder = fill.size - p.size;
                    self.positions.remove(&key);

                    // Flip: open opposite with remainder
                    if remainder > 0.0001 {
                        self.positions.insert(key, PositionState {
                            market_id: fill.market_id,
                            venue_id: fill.venue_id,
                            side: fill.side,
                            size: remainder,
                            entry_price: fill.price,
                            entry_ts: fill.ts,
                            unrealized_pnl: 0.0,
                            funding_paid: 0.0,
                            borrow_cumulative_at_entry: 0.0,
                        });
                    }
                } else {
                    // Partial close
                    let pnl = match p.side {
                        Side::Long => (fill.price - p.entry_price) * fill.size,
                        Side::Short => (p.entry_price - fill.price) * fill.size,
                    };
                    let pnl = (pnl * 1e8).round() / 1e8;

                    self.cash += pnl;
                    self.closed_trades.push(ClosedTrade {
                        market_id: fill.market_id,
                        venue_id: fill.venue_id,
                        side: p.side,
                        size: fill.size,
                        entry_price: p.entry_price,
                        exit_price: fill.price,
                        entry_ts: p.entry_ts,
                        exit_ts: fill.ts,
                        pnl,
                        funding_paid: 0.0,
                        borrow_paid: 0.0,
                        liquidated: false,
                        partial: true,
                    });

                    let pos_mut = self.positions.get_mut(&key).expect("position must exist after match");
                    pos_mut.size -= fill.size;
                }
            }
        }
    }

    /// Apply a funding payment to all positions matching a market.
    pub fn apply_funding(&mut self, market_id: MarketId, rate: f64, oracle_price: f64, candle_close: f64) -> f64 {
        let price = if oracle_price > 0.0 { oracle_price } else { candle_close };
        let mut total_payment = 0.0;
        for ((_, mid), pos) in self.positions.iter_mut() {
            if *mid != market_id { continue; }
            let notional = pos.size * price;
            let payment = match pos.side {
                Side::Long => notional * rate,
                Side::Short => -notional * rate,
            };
            pos.funding_paid += payment;
            total_payment += payment;
        }
        self.cash -= total_payment;
        total_payment
    }

    /// Force-close all positions at given prices, charging the caller-supplied
    /// FeeModel on each synthetic exit fill.
    ///
    /// D-1.1.b fix — previously the synthetic fill was built with fee=0.0,
    /// producing Rust/Python divergence at force-close (Python engine did
    /// charge fees, relaxed tolerance was captured in tests/test_rust_python_parity.py).
    /// Now both engines charge the same exit fee.
    pub fn close_all(
        &mut self,
        prices: &HashMap<MarketId, f64>,
        ts: i64,
        fee_model: &crate::engine::fees::FeeModel,
    ) {
        let keys: Vec<PosKey> = self.positions.keys().cloned().collect();
        for key in keys {
            let pos = self.positions.get(&key).expect("position must exist for key in iter").clone();
            let price = prices.get(&pos.market_id).copied().unwrap_or(pos.entry_price);
            let mut fill = FillResult {
                order_id: String::new(),
                market_id: pos.market_id,
                venue_id: pos.venue_id,
                side: pos.side.opposite(),
                price,
                size: pos.size,
                fee: 0.0,
                ts,
                is_partial: false,
                latency_ms: 0.0,
                impact_bps: 0.0,
                tx_cost: 0.0,
            };
            fill.fee = fee_model.compute_fee(&fill);
            self.apply_fill(&fill);
        }
    }

    pub fn add_log(&mut self, msg: String) {
        self.log_messages.push(msg);
    }
}
