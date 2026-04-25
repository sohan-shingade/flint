//! flint-core: Rust backtesting engine exposed to Python via PyO3.

mod engine;
mod runner;
mod types;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

use engine::fees::FeeModel;
use engine::tx_costs::{TxCostModel as RustTxCostModel, Urgency};
use runner::{BacktestRunner, RunConfig};
use types::*;

/// Convert Python side string to Rust Side enum.
fn parse_side(s: &str) -> Side {
    match s {
        "long" => Side::Long,
        "short" => Side::Short,
        _ => Side::Long,
    }
}

fn side_to_str(s: Side) -> &'static str {
    match s {
        Side::Long => "long",
        Side::Short => "short",
    }
}

fn parse_order_type(s: &str) -> OrderType {
    match s {
        "market" => OrderType::Market,
        "limit" => OrderType::Limit,
        "stop_loss" => OrderType::StopLoss,
        "take_profit" => OrderType::TakeProfit,
        _ => OrderType::Market,
    }
}

fn parse_fill_model(s: &str) -> FillModelType {
    match s {
        "close" => FillModelType::ClosePrice,
        "next_bar_open" => FillModelType::NextBarOpen,
        "slippage" => FillModelType::Slippage,
        "orderbook" => FillModelType::Orderbook,
        "pipeline" => FillModelType::Pipeline,
        _ => FillModelType::Pipeline,
    }
}

/// The main Python-facing engine class.
#[pyclass]
struct RustEngine {
    runner: BacktestRunner,
    registry: NameRegistry,
    seed: u64,
}

#[pymethods]
impl RustEngine {
    #[new]
    #[pyo3(signature = (initial_capital=10000.0, fee_bps=5.0, fill_model="pipeline", slippage_bps=5.0, seed=0, fee_model="flat", maker_bps=None, taker_bps=None))]
    fn new(
        initial_capital: f64,
        fee_bps: f64,
        fill_model: &str,
        slippage_bps: f64,
        seed: u64,
        fee_model: &str,
        maker_bps: Option<f64>,
        taker_bps: Option<f64>,
    ) -> Self {
        // D-3.3: select the fee-model variant by name. "flat" keeps the
        // pre-existing behavior (ignores maker/taker). "drift" and
        // "hyperliquid" pick the venue-specific maker-rebate schedule.
        // "maker_taker" lets callers pass explicit bps values.
        let fee = match fee_model {
            "drift" => FeeModel::Drift,
            "hyperliquid" => FeeModel::Hyperliquid,
            "maker_taker" => FeeModel::MakerTaker {
                maker_bps: maker_bps.unwrap_or(0.0),
                taker_bps: taker_bps.unwrap_or(fee_bps),
            },
            _ => FeeModel::Flat { fee_bps },
        };
        let config = RunConfig {
            initial_capital,
            fee_model: fee,
            fill_model_type: parse_fill_model(fill_model),
            slippage_bps,
            max_runtime_s: 300.0,
        };
        Self {
            runner: BacktestRunner::new(config),
            registry: NameRegistry::new(),
            seed,
        }
    }

    /// Load candles from Python list of dicts or tuples.
    /// Each candle: (ts, open, high, low, close, volume, market, resolution_s, venue)
    fn load_candles(&mut self, candles: &Bound<'_, PyList>) -> PyResult<()> {
        let mut bars = Vec::with_capacity(candles.len());
        for item in candles.iter() {
            let ts: i64 = item.getattr("ts")?.extract()?;
            let open: f64 = item.getattr("open")?.extract()?;
            let high: f64 = item.getattr("high")?.extract()?;
            let low: f64 = item.getattr("low")?.extract()?;
            let close: f64 = item.getattr("close")?.extract()?;
            let volume: f64 = item.getattr("volume")?.extract()?;
            let market: String = item.getattr("market")?.extract()?;
            let resolution_s: i32 = item.getattr("resolution_s")?.extract()?;
            let venue: String = item.getattr("venue")?.extract()?;

            let market_id = self.registry.get_or_insert_market(&market);
            let venue_id = self.registry.get_or_insert_venue(&venue);

            bars.push(CandleBar {
                ts, open, high, low, close, volume, resolution_s, market_id, venue_id,
            });
        }
        // Auto-register venue-specific fill pipelines for all venues seen
        let mut seen_venues = std::collections::HashSet::new();
        for bar in &bars {
            seen_venues.insert(bar.venue_id);
        }
        self.runner.load_candles(bars);
        for vid in seen_venues {
            let name = self.registry.venue_name(vid).to_string();
            use crate::engine::venue_fills::VenueFiller;
            // Seed is threaded from Python via RustEngine::new; each venue filler
            // gets a deterministic seed offset by venue_id so per-venue RNGs
            // are independent but reproducible. Phase 1 T1.1.c.
            let venue_seed = self.seed.wrapping_add(vid.0 as u64);
            let filler = VenueFiller::for_venue(&name, venue_seed);
            self.runner.register_venue_filler(vid, filler);
        }
        Ok(())
    }

    /// Load funding rates from Python list.
    fn load_funding(&mut self, rates: &Bound<'_, PyList>) -> PyResult<()> {
        let mut data = Vec::with_capacity(rates.len());
        for item in rates.iter() {
            let ts: i64 = item.getattr("ts")?.extract()?;
            let rate: f64 = item.getattr("rate")?.extract()?;
            let market: String = item.getattr("market")?.extract()?;
            let source: String = item.getattr("source")?.extract()?;
            let oracle_price: f64 = item.getattr("oracle_price")?.extract()?;

            let market_id = self.registry.get_or_insert_market(&market);
            let venue_id = self.registry.get_or_insert_venue(&source);

            data.push(FundingRateData { ts, rate, market_id, venue_id, oracle_price });
        }
        self.runner.load_funding(data);
        Ok(())
    }

    /// Number of candles loaded.
    fn num_candles(&self) -> usize {
        self.runner.num_candles()
    }

    /// Pre-bar processing. Returns dict with bar state.
    fn pre_bar(&mut self, py: Python<'_>, bar_idx: usize) -> PyResult<PyObject> {
        let state = self.runner.pre_bar(bar_idx);
        let dict = PyDict::new(py);
        dict.set_item("bar_index", state.bar_index)?;
        dict.set_item("equity", state.equity)?;
        dict.set_item("cash", state.cash)?;
        dict.set_item("unrealized_pnl", state.unrealized_pnl)?;
        dict.set_item("num_positions", state.num_positions)?;
        dict.set_item("num_pending_orders", state.num_pending_orders)?;
        Ok(dict.into())
    }

    /// Submit a market order.
    fn submit_market_order(
        &mut self,
        market: &str,
        side: &str,
        size: f64,
        venue: &str,
    ) -> String {
        let oid = self.runner.next_order_id();
        let market_id = self.registry.get_or_insert_market(market);
        let venue_id = self.registry.get_or_insert_venue(venue);
        self.runner.submit_market_order(OrderData {
            order_id: oid.clone(),
            market_id,
            venue_id,
            side: parse_side(side),
            order_type: OrderType::Market,
            size,
            price: 0.0,
            ts: 0,
            reduce_only: false,
            time_in_force: TimeInForce::Ioc,
        });
        oid
    }

    /// Submit a limit order.
    fn submit_limit_order(
        &mut self,
        market: &str,
        side: &str,
        size: f64,
        price: f64,
        venue: &str,
    ) -> String {
        let oid = self.runner.next_order_id();
        let market_id = self.registry.get_or_insert_market(market);
        let venue_id = self.registry.get_or_insert_venue(venue);
        self.runner.submit_limit_order(OrderData {
            order_id: oid.clone(),
            market_id,
            venue_id,
            side: parse_side(side),
            order_type: OrderType::Limit,
            size,
            price,
            ts: 0,
            reduce_only: false,
            time_in_force: TimeInForce::Gtc,
        });
        oid
    }

    /// Submit a stop order.
    fn submit_stop_order(
        &mut self,
        market: &str,
        side: &str,
        size: f64,
        trigger_price: f64,
        venue: &str,
    ) -> String {
        let oid = self.runner.next_order_id();
        let market_id = self.registry.get_or_insert_market(market);
        let venue_id = self.registry.get_or_insert_venue(venue);
        self.runner.submit_stop_order(OrderData {
            order_id: oid.clone(),
            market_id,
            venue_id,
            side: parse_side(side),
            order_type: OrderType::StopLoss,
            size,
            price: trigger_price,
            ts: 0,
            reduce_only: false,
            time_in_force: TimeInForce::Gtc,
        });
        oid
    }

    /// Post-bar processing.
    fn post_bar(&mut self, bar_idx: usize) {
        self.runner.post_bar(bar_idx);
    }

    /// Finalize and return results as a dict.
    fn finish(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let result = self.runner.finish();
        let dict = PyDict::new(py);

        dict.set_item("total_pnl", result.total_pnl)?;
        dict.set_item("win_rate", result.win_rate)?;
        dict.set_item("max_drawdown", result.max_drawdown)?;
        dict.set_item("sharpe_ratio", result.sharpe_ratio)?;
        dict.set_item("total_trades", result.total_trades)?;
        dict.set_item("winning_trades", result.winning_trades)?;
        dict.set_item("losing_trades", result.losing_trades)?;
        dict.set_item("equity_curve", result.equity_curve)?;
        dict.set_item("total_fees", result.total_fees)?;
        dict.set_item("funding_paid", result.funding_paid)?;
        dict.set_item("total_tx_costs", result.total_tx_costs)?;

        // Per-venue PnL
        let pv_pnl = PyDict::new(py);
        for (vid, pnl) in &result.per_venue_pnl {
            pv_pnl.set_item(self.registry.venue_name(*vid), pnl)?;
        }
        dict.set_item("per_venue_pnl", pv_pnl)?;

        // Per-venue trades
        let pv_trades = PyDict::new(py);
        for (vid, count) in &result.per_venue_trades {
            pv_trades.set_item(self.registry.venue_name(*vid), count)?;
        }
        dict.set_item("per_venue_trades", pv_trades)?;

        // Per-venue funding
        let pv_funding = PyDict::new(py);
        for (vid, amount) in &result.per_venue_funding {
            pv_funding.set_item(self.registry.venue_name(*vid), amount)?;
        }
        dict.set_item("per_venue_funding", pv_funding)?;

        // Closed trades as list of dicts
        let trades_list = PyList::empty(py);
        for trade in &result.closed_trades {
            let td = PyDict::new(py);
            td.set_item("market", self.registry.market_name(trade.market_id))?;
            td.set_item("venue", self.registry.venue_name(trade.venue_id))?;
            td.set_item("side", side_to_str(trade.side))?;
            td.set_item("size", trade.size)?;
            td.set_item("entry_price", trade.entry_price)?;
            td.set_item("exit_price", trade.exit_price)?;
            td.set_item("entry_ts", trade.entry_ts)?;
            td.set_item("exit_ts", trade.exit_ts)?;
            td.set_item("pnl", trade.pnl)?;
            td.set_item("funding_paid", trade.funding_paid)?;
            trades_list.append(td)?;
        }
        dict.set_item("closed_trades", trades_list)?;

        dict.set_item("log_messages", result.log_messages)?;

        Ok(dict.into())
    }

    /// Get current positions as list of dicts.
    fn get_positions(&self, py: Python<'_>) -> PyResult<PyObject> {
        let list = PyList::empty(py);
        for (vid, mid, side, size, entry_price, upnl) in self.runner.get_position_info() {
            let d = PyDict::new(py);
            d.set_item("market", self.registry.market_name(mid))?;
            d.set_item("venue", self.registry.venue_name(vid))?;
            d.set_item("side", side_to_str(side))?;
            d.set_item("size", size)?;
            d.set_item("entry_price", entry_price)?;
            d.set_item("unrealized_pnl", upnl)?;
            list.append(d)?;
        }
        Ok(list.into())
    }

    fn cash(&self) -> f64 { self.runner.cash() }
    fn equity(&self) -> f64 { self.runner.equity() }
}

/// Capability flags exposed to Python.
///
/// Phase 3 T3.2. Drives the `can_use_rust` dispatch in BacktestEngine and
/// the `--rust-required` gate. Keep this in sync with what runner.rs /
/// engine/* actually implements; lying here = silent wrong numbers.
#[pyfunction]
fn capabilities(py: Python<'_>) -> PyResult<PyObject> {
    let d = PyDict::new(py);

    // Fill models actually supported by runner.rs dispatch
    let fill_models = PyList::new(py, ["close", "next_bar_open", "slippage",
                                        "pipeline"])?;
    d.set_item("fill_models", fill_models)?;

    // Fee models exposed to Python. D-3.3 adds maker_taker / drift /
    // hyperliquid on top of the baseline flat model so callers can
    // test maker rebates end-to-end through the Rust engine.
    let fee_models = PyList::new(py, ["flat", "maker_taker", "drift", "hyperliquid"])?;
    d.set_item("fee_models", fee_models)?;

    // Feature flags — the Python-side can_use_rust gate mirrors these.
    d.set_item("supports_partial_fills", false)?;   // T3.4
    d.set_item("supports_latency_stage", false)?;   // T3.4
    d.set_item("supports_tx_costs", true)?;         // D-3.4-rust
    d.set_item("supports_orderbook_walk", false)?;  // T3.1 Rust port
    d.set_item("supports_cross_market", false)?;    // Phase 2 follow-up
    d.set_item("supports_multi_venue_margin", false)?;  // T3.5
    d.set_item("supports_borrow_snapshots", false)?;
    d.set_item("supports_maker_taker_fees", true)?;     // D-3.3 (Wave 2)

    // Engine identity + version — helps UI surface which engine ran.
    d.set_item("engine", "rust")?;
    d.set_item("engine_version", env!("CARGO_PKG_VERSION"))?;
    Ok(d.into())
}

// ----- TxCostModel PyO3 bindings (D-3.4-rust) -----
//
// The Python side exposes three concrete classes under
// `flint.execution.tx_costs`. Rather than mirror the full class
// hierarchy in PyO3 (three #[pyclass]es all returning the same dict),
// this wrapper exposes a single factory + `.estimate()` method that
// dispatches on venue. `test_rust_tx_cost_parity.py` verifies the
// outputs match the Python models component-by-component.

/// PyO3 wrapper around `engine::tx_costs::TxCostModel`.
#[pyclass(name = "TxCostModel")]
struct PyTxCostModel {
    inner: RustTxCostModel,
}

#[pymethods]
impl PyTxCostModel {
    /// Construct from a venue name. Falls back to the CEX model for
    /// unknown venues — matches the Python `get_tx_cost_model()`
    /// factory 1:1.
    #[staticmethod]
    fn for_venue(venue: &str) -> Self {
        PyTxCostModel { inner: RustTxCostModel::for_venue(venue) }
    }

    /// Drift / Solana defaults (5 bps taker, 5_000 lamports priority,
    /// 10_000 lamports Jito tip, $150 SOL).
    #[staticmethod]
    #[pyo3(signature = (priority_fee_lamports=5_000, jito_tip_lamports=10_000, sol_price_usd=150.0, exchange_fee_bps=5.0, historical_p50=None, historical_p90=None))]
    fn solana(
        priority_fee_lamports: u64,
        jito_tip_lamports: u64,
        sol_price_usd: f64,
        exchange_fee_bps: f64,
        historical_p50: Option<u64>,
        historical_p90: Option<u64>,
    ) -> Self {
        PyTxCostModel {
            inner: RustTxCostModel::Solana {
                priority_fee_lamports,
                jito_tip_lamports,
                sol_price_usd,
                exchange_fee_bps,
                historical_p50,
                historical_p90,
            },
        }
    }

    #[staticmethod]
    #[pyo3(signature = (l1_cost_usd=0.001, exchange_fee_bps=3.5))]
    fn hyperliquid(l1_cost_usd: f64, exchange_fee_bps: f64) -> Self {
        PyTxCostModel {
            inner: RustTxCostModel::Hyperliquid { l1_cost_usd, exchange_fee_bps },
        }
    }

    #[staticmethod]
    #[pyo3(signature = (exchange_fee_bps=5.0))]
    fn cex(exchange_fee_bps: f64) -> Self {
        PyTxCostModel {
            inner: RustTxCostModel::Cex { exchange_fee_bps },
        }
    }

    /// Estimate cost for a trade. Returns a dict with the same keys
    /// as Python `CostEstimate.to_dict()`: exchange_fee, network_fee,
    /// bundle_tip, impact_est, total.
    #[pyo3(signature = (market, size, price, urgency="normal"))]
    fn estimate(
        &self,
        py: Python<'_>,
        market: &str,
        size: f64,
        price: f64,
        urgency: &str,
    ) -> PyResult<PyObject> {
        let est = self.inner.estimate(market, size, price, Urgency::from_str(urgency));
        let d = PyDict::new(py);
        d.set_item("exchange_fee", est.exchange_fee)?;
        d.set_item("network_fee", est.network_fee)?;
        d.set_item("bundle_tip", est.bundle_tip)?;
        d.set_item("impact_est", est.impact_est)?;
        d.set_item("total", est.total())?;
        Ok(d.into())
    }
}

/// Python module definition.
#[pymodule]
fn flint_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustEngine>()?;
    m.add_class::<PyTxCostModel>()?;
    m.add_function(wrap_pyfunction!(capabilities, m)?)?;
    Ok(())
}
