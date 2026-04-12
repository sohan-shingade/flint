//! Top-level backtest runner — orchestrates the per-bar loop.

use std::collections::HashMap;
use crate::types::*;
use crate::engine::{fees::FeeModel, metrics, orders, positions::PositionManager};

/// Configuration for a backtest run.
pub struct RunConfig {
    pub initial_capital: f64,
    pub fee_model: FeeModel,
    pub fill_model_type: FillModelType,
    pub slippage_bps: f64,
    pub max_runtime_s: f64,
}

/// State exposed to Python between pre_bar and post_bar.
pub struct BarState {
    pub bar_index: usize,
    pub equity: f64,
    pub cash: f64,
    pub unrealized_pnl: f64,
    pub num_positions: usize,
    pub num_pending_orders: usize,
}

/// The Rust backtest engine core.
pub struct BacktestRunner {
    config: RunConfig,
    pos_mgr: PositionManager,
    pending_orders: Vec<OrderData>,
    market_orders_queue: Vec<OrderData>,
    all_fills: Vec<FillResult>,
    equity_curve: Vec<f64>,
    total_funding: f64,
    per_venue_funding: HashMap<VenueId, f64>,

    // Auxiliary data (pre-sorted, cursor-based)
    funding_rates: Vec<FundingRateData>,
    funding_cursor: usize,

    // Candle data
    candles: Vec<CandleBar>,
    extra_markets: HashMap<MarketId, Vec<CandleBar>>,
    extra_cursors: HashMap<MarketId, usize>,

    order_counter: u64,
}

impl BacktestRunner {
    pub fn new(config: RunConfig) -> Self {
        let pos_mgr = PositionManager::new(config.initial_capital);
        Self {
            config,
            pos_mgr,
            pending_orders: Vec::new(),
            market_orders_queue: Vec::new(),
            all_fills: Vec::new(),
            equity_curve: Vec::new(),
            total_funding: 0.0,
            per_venue_funding: HashMap::new(),
            funding_rates: Vec::new(),
            funding_cursor: 0,
            candles: Vec::new(),
            extra_markets: HashMap::new(),
            extra_cursors: HashMap::new(),
            order_counter: 0,
        }
    }

    pub fn load_candles(&mut self, candles: Vec<CandleBar>) {
        self.candles = candles;
    }

    pub fn load_extra_markets(&mut self, extra: HashMap<MarketId, Vec<CandleBar>>) {
        for (mid, candles) in &extra {
            self.extra_cursors.insert(*mid, 0);
        }
        self.extra_markets = extra;
    }

    pub fn load_funding(&mut self, mut rates: Vec<FundingRateData>) {
        rates.sort_by_key(|r| r.ts);
        self.funding_rates = rates;
        self.funding_cursor = 0;
    }

    pub fn num_candles(&self) -> usize {
        self.candles.len()
    }

    /// Pre-bar processing: advance cursors, process pending orders, apply funding.
    /// Returns state for Python to use during strategy callback.
    pub fn pre_bar(&mut self, bar_idx: usize) -> BarState {
        let candle = self.candles[bar_idx];

        // Update unrealized PnL
        self.pos_mgr.update_pnl_for_market(candle.market_id, candle.close);

        // Process pending stop/limit orders
        let fills = orders::process_pending_orders(
            &mut self.pending_orders,
            &candle,
            &self.config.fee_model,
        );
        for fill in &fills {
            self.pos_mgr.apply_fill(fill);
        }
        self.all_fills.extend(fills);

        // Advance funding cursor and apply
        while self.funding_cursor < self.funding_rates.len()
            && self.funding_rates[self.funding_cursor].ts <= candle.ts
        {
            let fr = &self.funding_rates[self.funding_cursor];
            let payment = self.pos_mgr.apply_funding(
                fr.market_id, fr.rate, fr.oracle_price, candle.close);
            self.total_funding += payment;
            *self.per_venue_funding.entry(fr.venue_id).or_insert(0.0) += payment;
            self.funding_cursor += 1;
        }

        BarState {
            bar_index: bar_idx,
            equity: self.pos_mgr.equity(),
            cash: self.pos_mgr.cash(),
            unrealized_pnl: self.pos_mgr.equity() - self.pos_mgr.cash(),
            num_positions: self.pos_mgr.positions.len(),
            num_pending_orders: self.pending_orders.len(),
        }
    }

    /// Submit orders from the strategy (called between pre_bar and post_bar).
    pub fn submit_market_order(&mut self, order: OrderData) {
        self.market_orders_queue.push(order);
    }

    pub fn submit_limit_order(&mut self, order: OrderData) {
        if self.pending_orders.len() < 100 {
            self.pending_orders.push(order);
        }
    }

    pub fn submit_stop_order(&mut self, order: OrderData) {
        if self.pending_orders.len() < 100 {
            self.pending_orders.push(order);
        }
    }

    pub fn next_order_id(&mut self) -> String {
        self.order_counter += 1;
        format!("bt-{}", self.order_counter)
    }

    /// Post-bar processing: execute market orders, record equity.
    pub fn post_bar(&mut self, bar_idx: usize) {
        let candle = self.candles[bar_idx];

        // Process market orders
        let fills = orders::process_market_orders(
            &mut self.market_orders_queue,
            &candle,
            &self.config.fee_model,
            self.config.fill_model_type,
            self.config.slippage_bps,
        );
        for fill in &fills {
            self.pos_mgr.apply_fill(fill);
        }
        self.all_fills.extend(fills);

        // Record equity
        self.equity_curve.push(self.pos_mgr.equity());
    }

    /// Finalize: close all positions, compute metrics.
    pub fn finish(&mut self) -> EngineResult {
        // Force-close all open positions at last candle
        if !self.candles.is_empty() && !self.pos_mgr.positions.is_empty() {
            let last = self.candles.last().expect("candles non-empty checked above");
            let mut prices = HashMap::new();
            prices.insert(last.market_id, last.close);
            self.pos_mgr.close_all(&prices, last.ts);
            // Update last equity entry
            if let Some(last_eq) = self.equity_curve.last_mut() {
                *last_eq = self.pos_mgr.equity();
            }
        }

        let trades = &self.pos_mgr.closed_trades;
        let winners: Vec<_> = trades.iter().filter(|t| t.pnl > 0.0).collect();
        let losers: Vec<_> = trades.iter().filter(|t| t.pnl <= 0.0).collect();
        let total_trades = trades.len();
        let win_rate = if total_trades > 0 {
            winners.len() as f64 / total_trades as f64
        } else {
            0.0
        };

        let res_s = if !self.candles.is_empty() {
            self.candles[0].resolution_s
        } else {
            3600
        };
        let periods_per_year = if res_s > 0 {
            365.25 * 24.0 * 3600.0 / res_s as f64
        } else {
            8760.0
        };

        // Per-venue aggregation
        let mut per_venue_pnl: HashMap<VenueId, f64> = HashMap::new();
        let mut per_venue_trades: HashMap<VenueId, usize> = HashMap::new();
        for trade in trades {
            *per_venue_pnl.entry(trade.venue_id).or_insert(0.0) += trade.pnl;
            *per_venue_trades.entry(trade.venue_id).or_insert(0) += 1;
        }

        EngineResult {
            total_pnl: self.pos_mgr.equity() - self.config.initial_capital,
            win_rate,
            max_drawdown: metrics::max_drawdown(&self.equity_curve),
            sharpe_ratio: metrics::sharpe_ratio(&self.equity_curve, periods_per_year),
            total_trades,
            winning_trades: winners.len(),
            losing_trades: losers.len(),
            equity_curve: self.equity_curve.clone(),
            fills: self.all_fills.clone(),
            closed_trades: self.pos_mgr.closed_trades.clone(),
            total_fees: self.pos_mgr.total_fees(),
            funding_paid: self.total_funding,
            total_tx_costs: self.pos_mgr.total_tx_costs(),
            total_borrow_paid: self.pos_mgr.total_borrow_paid(),
            per_venue_pnl,
            per_venue_trades,
            per_venue_funding: self.per_venue_funding.clone(),
            log_messages: self.pos_mgr.log_messages().to_vec(),
        }
    }

    // Accessors for Python wrapper
    pub fn get_position_info(&self) -> Vec<(VenueId, MarketId, Side, f64, f64, f64)> {
        self.pos_mgr.positions.values().map(|p| {
            (p.venue_id, p.market_id, p.side, p.size, p.entry_price, p.unrealized_pnl)
        }).collect()
    }

    pub fn get_candle(&self, idx: usize) -> Option<&CandleBar> {
        self.candles.get(idx)
    }

    pub fn cash(&self) -> f64 { self.pos_mgr.cash() }
    pub fn equity(&self) -> f64 { self.pos_mgr.equity() }
}
