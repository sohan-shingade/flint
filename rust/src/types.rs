//! Core data types for the Rust backtesting engine.
//!
//! Mirrors the Python models in `flint/models.py` with cache-friendly layout.

use pyo3::prelude::*;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Side {
    Long,
    Short,
}

impl Side {
    pub fn opposite(self) -> Side {
        match self {
            Side::Long => Side::Short,
            Side::Short => Side::Long,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderType {
    Market,
    Limit,
    StopLoss,
    TakeProfit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimeInForce {
    Ioc,
    Fok,
    Gtc,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FillModelType {
    ClosePrice,
    NextBarOpen,
    Slippage,
    Orderbook,
    Pipeline,
}

/// Venue identifier — uses u16 index for HashMap efficiency.
/// String venue names are mapped to IDs at engine init.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct VenueId(pub u16);

/// Market identifier — uses u16 index.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct MarketId(pub u16);

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

/// Single OHLCV bar — 56 bytes, contiguous in Vec<CandleBar>.
#[derive(Debug, Clone, Copy)]
pub struct CandleBar {
    pub ts: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub resolution_s: i32,
    pub market_id: MarketId,
    pub venue_id: VenueId,
}

/// Contiguous candle storage for one market.
#[derive(Debug, Clone)]
pub struct MarketData {
    pub candles: Vec<CandleBar>,
    pub market_id: MarketId,
}

/// Order submitted by the strategy.
#[derive(Debug, Clone)]
pub struct OrderData {
    pub order_id: String,
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub side: Side,
    pub order_type: OrderType,
    pub size: f64,
    pub price: f64, // limit/stop price; 0 for market
    pub ts: i64,
    pub reduce_only: bool,
    pub time_in_force: TimeInForce,
}

/// Fill result.
#[derive(Debug, Clone)]
pub struct FillResult {
    pub order_id: String,
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub side: Side,
    pub price: f64,
    pub size: f64,
    pub fee: f64,
    pub ts: i64,
    pub is_partial: bool,
    pub latency_ms: f64,
    pub impact_bps: f64,
    pub tx_cost: f64,
    /// D-3.3: true when the fill was a resting-order (passive) fill, i.e.
    /// a pending limit order that was taken by another participant. False
    /// for market orders and for limit orders that cross the book on
    /// submission. Drives maker-rebate vs taker-fee selection.
    pub is_maker: bool,
}

/// Funding rate record.
#[derive(Debug, Clone)]
pub struct FundingRateData {
    pub ts: i64,
    pub rate: f64,
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub oracle_price: f64,
}

/// Orderbook level.
#[derive(Debug, Clone, Copy)]
pub struct BookLevel {
    pub price: f64,
    pub size: f64,
}

/// Orderbook snapshot.
#[derive(Debug, Clone)]
pub struct OrderbookData {
    pub ts: i64,
    pub market_id: MarketId,
    pub bids: Vec<BookLevel>,
    pub asks: Vec<BookLevel>,
}

/// Open interest record.
#[derive(Debug, Clone, Copy)]
pub struct OiData {
    pub ts: i64,
    pub market_id: MarketId,
    pub long_oi: f64,
    pub short_oi: f64,
}

/// Borrow rate record (Jupiter Perps).
#[derive(Debug, Clone, Copy)]
pub struct BorrowData {
    pub ts: i64,
    pub market_id: MarketId,
    pub rate_hourly: f64,
    pub cumulative_rate: f64,
}

// ---------------------------------------------------------------------------
// Position state
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct PositionState {
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub side: Side,
    pub size: f64,
    pub entry_price: f64,
    pub entry_ts: i64,
    pub unrealized_pnl: f64,
    pub funding_paid: f64,
    pub borrow_cumulative_at_entry: f64,
}

impl PositionState {
    pub fn update_pnl(&mut self, current_price: f64) {
        self.unrealized_pnl = match self.side {
            Side::Long => (current_price - self.entry_price) * self.size,
            Side::Short => (self.entry_price - current_price) * self.size,
        };
    }
}

// ---------------------------------------------------------------------------
// Account state
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct AccountSnapshot {
    pub equity: f64,
    pub cash: f64,
    pub unrealized_pnl: f64,
    pub margin_used: f64,
    pub free_margin: f64,
    pub leverage: f64,
}

// ---------------------------------------------------------------------------
// Closed trade record
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct ClosedTrade {
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub side: Side,
    pub size: f64,
    pub entry_price: f64,
    pub exit_price: f64,
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub pnl: f64,
    pub funding_paid: f64,
    pub borrow_paid: f64,
    pub liquidated: bool,
    pub partial: bool,
}

// ---------------------------------------------------------------------------
// Engine result
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct EngineResult {
    pub total_pnl: f64,
    pub win_rate: f64,
    pub max_drawdown: f64,
    pub sharpe_ratio: f64,
    pub total_trades: usize,
    pub winning_trades: usize,
    pub losing_trades: usize,
    pub equity_curve: Vec<f64>,
    pub fills: Vec<FillResult>,
    pub closed_trades: Vec<ClosedTrade>,
    pub total_fees: f64,
    pub funding_paid: f64,
    pub total_tx_costs: f64,
    pub total_borrow_paid: f64,
    pub per_venue_pnl: HashMap<VenueId, f64>,
    pub per_venue_trades: HashMap<VenueId, usize>,
    pub per_venue_funding: HashMap<VenueId, f64>,
    pub log_messages: Vec<String>,
}

// ---------------------------------------------------------------------------
// Name registry — maps string names to u16 IDs
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default)]
pub struct NameRegistry {
    market_names: Vec<String>,
    market_map: HashMap<String, MarketId>,
    venue_names: Vec<String>,
    venue_map: HashMap<String, VenueId>,
}

impl NameRegistry {
    pub fn new() -> Self {
        let mut reg = Self::default();
        // Pre-register "default" venue
        reg.get_or_insert_venue("default");
        reg
    }

    pub fn get_or_insert_market(&mut self, name: &str) -> MarketId {
        if let Some(&id) = self.market_map.get(name) {
            return id;
        }
        let id = MarketId(self.market_names.len() as u16);
        self.market_names.push(name.to_string());
        self.market_map.insert(name.to_string(), id);
        id
    }

    pub fn get_or_insert_venue(&mut self, name: &str) -> VenueId {
        if let Some(&id) = self.venue_map.get(name) {
            return id;
        }
        let id = VenueId(self.venue_names.len() as u16);
        self.venue_names.push(name.to_string());
        self.venue_map.insert(name.to_string(), id);
        id
    }

    pub fn market_name(&self, id: MarketId) -> &str {
        &self.market_names[id.0 as usize]
    }

    pub fn venue_name(&self, id: VenueId) -> &str {
        &self.venue_names[id.0 as usize]
    }

    pub fn market_id(&self, name: &str) -> Option<MarketId> {
        self.market_map.get(name).copied()
    }

    pub fn venue_id(&self, name: &str) -> Option<VenueId> {
        self.venue_map.get(name).copied()
    }
}
