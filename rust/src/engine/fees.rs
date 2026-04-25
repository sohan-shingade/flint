//! Fee models — compute trade fees from fills.
//!
//! Phase 3 T3.3 — adds maker/taker rate support. Today's Rust fill pipeline
//! does not track maker vs taker per fill (all fills treated as taker); the
//! MakerTaker / Drift / Hyperliquid variants therefore apply the taker rate
//! unless the caller explicitly marks the fill as maker via
//! `compute_fee_with_role`. Maker-fill tracking lands with T3.4 partial-fill
//! + latency Rust port.

use crate::types::{FillResult, Side};

#[derive(Debug, Clone, Copy)]
pub enum FeeModel {
    Flat { fee_bps: f64 },
    /// Separate maker/taker in bps. Maker negative = rebate.
    MakerTaker { maker_bps: f64, taker_bps: f64 },
    /// Drift perps default (10 bps taker, -2 bps maker rebate).
    Drift,
    /// Hyperliquid default (3.5 bps taker, 1 bp maker).
    Hyperliquid,
    /// Legacy alias kept for back-compat; new code should use MakerTaker.
    DriftTiered { maker_fee: f64, taker_fee: f64 },
    Zero,
}

impl FeeModel {
    /// Fee defaulting to the taker rate.
    pub fn compute_fee(&self, fill: &FillResult) -> f64 {
        self.compute_fee_with_role(fill, false)
    }

    /// Fee with explicit maker/taker role. `is_maker=true` uses the maker
    /// rate (possibly negative = rebate); otherwise uses the taker rate.
    pub fn compute_fee_with_role(&self, fill: &FillResult, is_maker: bool) -> f64 {
        let notional = fill.size.abs() * fill.price;
        match self {
            FeeModel::Flat { fee_bps } => notional * fee_bps / 10_000.0,
            FeeModel::MakerTaker { maker_bps, taker_bps } => {
                let bps = if is_maker { *maker_bps } else { *taker_bps };
                notional * bps / 10_000.0
            }
            FeeModel::Drift => {
                // Drift perps: taker 10 bps, maker rebate -2 bps
                let bps = if is_maker { -2.0 } else { 10.0 };
                notional * bps / 10_000.0
            }
            FeeModel::Hyperliquid => {
                // HL standard perp tier: taker 3.5 bps, maker 1 bp
                let bps = if is_maker { 1.0 } else { 3.5 };
                notional * bps / 10_000.0
            }
            FeeModel::DriftTiered { maker_fee, taker_fee } => {
                let rate = if is_maker { *maker_fee } else { *taker_fee };
                notional * rate
            }
            FeeModel::Zero => 0.0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{FillResult, MarketId, Side, VenueId};

    fn _fill(size: f64, price: f64) -> FillResult {
        FillResult {
            order_id: String::new(),
            market_id: MarketId(0),
            venue_id: VenueId(0),
            side: Side::Long,
            price,
            size,
            fee: 0.0,
            ts: 0,
            is_partial: false,
            latency_ms: 0.0,
            impact_bps: 0.0,
            tx_cost: 0.0,
            is_maker: false,
        }
    }

    #[test]
    fn drift_taker_is_10_bps() {
        // 100 size * 50 price * 10/10000 = 5.0
        assert!((FeeModel::Drift.compute_fee(&_fill(100.0, 50.0)) - 5.0).abs() < 1e-9);
    }

    #[test]
    fn drift_maker_is_rebate() {
        // 100 * 50 * -2/10000 = -1.0 (maker rebate)
        let fee = FeeModel::Drift.compute_fee_with_role(&_fill(100.0, 50.0), true);
        assert!((fee - (-1.0)).abs() < 1e-9);
    }

    #[test]
    fn hyperliquid_taker_is_3_5_bps() {
        let fee = FeeModel::Hyperliquid.compute_fee(&_fill(100.0, 50.0));
        assert!((fee - 1.75).abs() < 1e-9);  // 100*50*3.5/10000
    }

    #[test]
    fn hyperliquid_maker_is_1_bp() {
        let fee = FeeModel::Hyperliquid.compute_fee_with_role(&_fill(100.0, 50.0), true);
        assert!((fee - 0.5).abs() < 1e-9);  // 100*50*1/10000
    }

    #[test]
    fn maker_taker_custom() {
        let m = FeeModel::MakerTaker { maker_bps: 2.0, taker_bps: 7.0 };
        assert!((m.compute_fee_with_role(&_fill(10.0, 100.0), true) - 0.2).abs() < 1e-9);
        assert!((m.compute_fee_with_role(&_fill(10.0, 100.0), false) - 0.7).abs() < 1e-9);
    }
}
