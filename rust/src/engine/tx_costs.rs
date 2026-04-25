//! Transaction cost models — Rust port of `flint/execution/tx_costs.py`.
//!
//! D-3.4-rust (Wave 1): ports the three venue cost models behind the
//! `TxCostModel` ABC into Rust so the hot path (engine fill loop
//! computing per-trade cost breakdowns) doesn't round-trip through
//! Python for every fill.
//!
//! Parity guarantee: every field on the Python `CostEstimate` dataclass
//! is computed identically to `CostEstimate` here, and
//! `tests/test_rust_tx_cost_parity.py` pins the agreement to 1e-9.

const LAMPORTS_PER_SOL: u64 = 1_000_000_000;

/// Full cost breakdown for one trade — mirrors the Python dataclass.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CostEstimate {
    pub exchange_fee: f64,
    pub network_fee: f64,
    pub bundle_tip: f64,
    pub impact_est: f64,
}

impl CostEstimate {
    pub fn total(&self) -> f64 {
        self.exchange_fee + self.network_fee + self.bundle_tip + self.impact_est
    }
}

/// The three venue cost shapes. Matches the Python class hierarchy
/// one-for-one; the historical_fees map (p50/p90) on the Solana model
/// is simplified to two optional fields here to avoid dragging a
/// HashMap into the hot path.
#[derive(Debug, Clone, Copy)]
pub enum TxCostModel {
    Solana {
        priority_fee_lamports: u64,
        jito_tip_lamports: u64,
        sol_price_usd: f64,
        exchange_fee_bps: f64,
        /// Historical p50 (normal). When None, falls back to `priority_fee_lamports`.
        historical_p50: Option<u64>,
        /// Historical p90 (urgent). When None, falls back to `priority_fee_lamports`.
        historical_p90: Option<u64>,
    },
    Hyperliquid {
        l1_cost_usd: f64,
        exchange_fee_bps: f64,
    },
    Cex {
        exchange_fee_bps: f64,
    },
}

/// Whether the caller wanted the "urgent" priority-fee tier. Drift uses
/// this to pick p90 over p50; other venues ignore it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Urgency {
    Normal,
    Urgent,
}

impl Urgency {
    pub fn from_str(s: &str) -> Self {
        match s {
            "urgent" => Urgency::Urgent,
            _ => Urgency::Normal,
        }
    }
}

impl TxCostModel {
    pub fn drift_default() -> Self {
        TxCostModel::Solana {
            priority_fee_lamports: 5_000,
            jito_tip_lamports: 10_000,
            sol_price_usd: 150.0,
            exchange_fee_bps: 5.0,
            historical_p50: None,
            historical_p90: None,
        }
    }

    pub fn hyperliquid_default() -> Self {
        TxCostModel::Hyperliquid {
            l1_cost_usd: 0.001,
            exchange_fee_bps: 3.5,
        }
    }

    pub fn cex_default() -> Self {
        TxCostModel::Cex { exchange_fee_bps: 5.0 }
    }

    /// Dispatch to the right model by venue name — mirrors the
    /// Python `get_tx_cost_model()` factory.
    pub fn for_venue(venue: &str) -> Self {
        match venue.to_lowercase().as_str() {
            "drift" => Self::drift_default(),
            "hyperliquid" => Self::hyperliquid_default(),
            _ => Self::cex_default(),
        }
    }

    /// Core estimate function. `urgency` selects p50/p90 on Solana.
    /// Market is accepted for API parity with Python but not used by
    /// any current model (future venue-specific quirks would hook in
    /// here).
    pub fn estimate(&self, _market: &str, size: f64, price: f64, urgency: Urgency) -> CostEstimate {
        let notional = size * price;
        match self {
            TxCostModel::Solana {
                priority_fee_lamports,
                jito_tip_lamports,
                sol_price_usd,
                exchange_fee_bps,
                historical_p50,
                historical_p90,
            } => {
                let exchange_fee = notional * exchange_fee_bps / 10_000.0;
                let priority_lamports = match urgency {
                    Urgency::Urgent => historical_p90.unwrap_or(*priority_fee_lamports),
                    Urgency::Normal => historical_p50.unwrap_or(*priority_fee_lamports),
                };
                let network_fee = lamports_to_usd(priority_lamports, *sol_price_usd);
                let bundle_tip = lamports_to_usd(*jito_tip_lamports, *sol_price_usd);
                CostEstimate {
                    exchange_fee,
                    network_fee,
                    bundle_tip,
                    impact_est: 0.0,
                }
            }
            TxCostModel::Hyperliquid {
                l1_cost_usd,
                exchange_fee_bps,
            } => CostEstimate {
                exchange_fee: notional * exchange_fee_bps / 10_000.0,
                network_fee: *l1_cost_usd,
                bundle_tip: 0.0,
                impact_est: 0.0,
            },
            TxCostModel::Cex { exchange_fee_bps } => CostEstimate {
                exchange_fee: notional * exchange_fee_bps / 10_000.0,
                network_fee: 0.0,
                bundle_tip: 0.0,
                impact_est: 0.0,
            },
        }
    }
}

fn lamports_to_usd(lamports: u64, sol_price_usd: f64) -> f64 {
    (lamports as f64 / LAMPORTS_PER_SOL as f64) * sol_price_usd
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f64 = 1e-9;

    #[test]
    fn solana_default_matches_python() {
        // 5 bps on $15_000 notional = 7.5
        // priority 5_000 lamports / 1e9 * 150 = 7.5e-4
        // jito    10_000 lamports / 1e9 * 150 = 1.5e-3
        let est = TxCostModel::drift_default().estimate("SOL-PERP", 100.0, 150.0, Urgency::Normal);
        assert!((est.exchange_fee - 7.5).abs() < EPS);
        assert!((est.network_fee - 0.00075).abs() < EPS);
        assert!((est.bundle_tip - 0.0015).abs() < EPS);
        assert!((est.impact_est - 0.0).abs() < EPS);
    }

    #[test]
    fn solana_urgent_picks_p90() {
        let m = TxCostModel::Solana {
            priority_fee_lamports: 1000,
            jito_tip_lamports: 0,
            sol_price_usd: 100.0,
            exchange_fee_bps: 0.0,
            historical_p50: Some(2000),
            historical_p90: Some(20_000),
        };
        let n = m.estimate("SOL-PERP", 1.0, 100.0, Urgency::Normal);
        let u = m.estimate("SOL-PERP", 1.0, 100.0, Urgency::Urgent);
        // urgent > normal when p90 > p50
        assert!(u.network_fee > n.network_fee);
        // network fee == lamports/1e9 * sol_price
        assert!((n.network_fee - (2000.0 / 1e9) * 100.0).abs() < EPS);
        assert!((u.network_fee - (20_000.0 / 1e9) * 100.0).abs() < EPS);
    }

    #[test]
    fn hyperliquid_defaults() {
        // 3.5 bps on $10_000 notional = 3.5
        let est = TxCostModel::hyperliquid_default().estimate("BTC-PERP", 1.0, 10_000.0, Urgency::Normal);
        assert!((est.exchange_fee - 3.5).abs() < EPS);
        assert!((est.network_fee - 0.001).abs() < EPS);
        assert!((est.bundle_tip - 0.0).abs() < EPS);
    }

    #[test]
    fn cex_defaults() {
        let est = TxCostModel::cex_default().estimate("BTC/USDT", 0.1, 70_000.0, Urgency::Normal);
        // 5 bps on 7_000 = 3.5
        assert!((est.exchange_fee - 3.5).abs() < EPS);
        assert!(est.network_fee.abs() < EPS);
        assert!(est.bundle_tip.abs() < EPS);
    }

    #[test]
    fn total_sums_components() {
        let est = CostEstimate {
            exchange_fee: 1.0,
            network_fee: 0.5,
            bundle_tip: 0.25,
            impact_est: 0.125,
        };
        assert!((est.total() - 1.875).abs() < EPS);
    }

    #[test]
    fn factory_routes_by_venue() {
        assert!(matches!(
            TxCostModel::for_venue("drift"),
            TxCostModel::Solana { .. }
        ));
        assert!(matches!(
            TxCostModel::for_venue("HYPERLIQUID"),
            TxCostModel::Hyperliquid { .. }
        ));
        assert!(matches!(
            TxCostModel::for_venue("binance"),
            TxCostModel::Cex { .. }
        ));
    }
}
