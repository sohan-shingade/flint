//! BorrowLedger — Rust port of `flint/execution/borrow_ledger.py`.
//!
//! D-6.4-replay (Wave 5 Rust ledgers). Owns Jupiter Perps borrow-rate
//! history + the running `total_paid` counter + per-trade payment
//! ledger. Linear scan for `cumulative_at(ts)` mirrors the Python
//! impl — snapshots are append-ordered by arrival, which matches
//! time order in the engine.

use std::collections::HashMap;

#[derive(Debug, Clone, Copy)]
pub struct BorrowPoint {
    pub ts: i64,
    pub rate_hourly: f64,
    pub cumulative_rate: f64,
}

#[derive(Debug, Clone)]
pub struct BorrowPayment {
    pub market: String,
    pub ts: i64,
    pub cost: f64,
    pub cum_entry: f64,
    pub cum_exit: f64,
}

#[derive(Debug, Default, Clone)]
pub struct BorrowLedger {
    history: HashMap<String, Vec<BorrowPoint>>,
    payments: Vec<BorrowPayment>,
    pub total_paid: f64,
}

impl BorrowLedger {
    pub fn new() -> Self { Self::default() }

    pub fn record(&mut self, market: &str, ts: i64, rate_hourly: f64, cumulative_rate: f64) {
        self.history.entry(market.to_string()).or_default().push(BorrowPoint {
            ts, rate_hourly, cumulative_rate,
        });
    }

    pub fn record_payment(&mut self, p: BorrowPayment) {
        self.payments.push(p);
    }

    pub fn add_paid(&mut self, amount: f64) {
        self.total_paid += amount;
    }

    pub fn latest(&self, market: &str) -> Option<f64> {
        self.history.get(market).and_then(|h| h.last()).map(|p| p.rate_hourly)
    }

    pub fn recent(&self, market: &str, lookback: usize) -> Vec<(i64, f64)> {
        let h = match self.history.get(market) { Some(h) => h, None => return Vec::new() };
        let start = h.len().saturating_sub(lookback);
        h[start..].iter().map(|p| (p.ts, p.rate_hourly)).collect()
    }

    /// Cumulative-rate value at-or-before `ts`. Linear walk because
    /// snapshots arrive append-ordered and an early-exit on the
    /// first point past `ts` is the right behavior.
    pub fn cumulative_at(&self, market: &str, ts: i64) -> Option<f64> {
        let h = self.history.get(market)?;
        let mut out: Option<f64> = None;
        for pt in h {
            if pt.ts <= ts {
                out = Some(pt.cumulative_rate);
            } else {
                break;
            }
        }
        out
    }

    pub fn payments(&self) -> &[BorrowPayment] { &self.payments }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn latest_and_cumulative() {
        let mut bl = BorrowLedger::new();
        bl.record("JUP-PERP", 10, 0.0001, 0.005);
        bl.record("JUP-PERP", 20, 0.0002, 0.010);
        bl.record("JUP-PERP", 30, 0.00015, 0.015);
        assert_eq!(bl.latest("JUP-PERP"), Some(0.00015));
        assert!(bl.cumulative_at("JUP-PERP", 5).is_none());      // before all
        assert_eq!(bl.cumulative_at("JUP-PERP", 10), Some(0.005));
        assert_eq!(bl.cumulative_at("JUP-PERP", 25), Some(0.010));
        assert_eq!(bl.cumulative_at("JUP-PERP", 100), Some(0.015));
    }

    #[test]
    fn payments_and_total() {
        let mut bl = BorrowLedger::new();
        bl.record_payment(BorrowPayment {
            market: "JUP-PERP".to_string(), ts: 1, cost: 1.5,
            cum_entry: 0.001, cum_exit: 0.002,
        });
        bl.add_paid(1.5);
        assert_eq!(bl.total_paid, 1.5);
        assert_eq!(bl.payments().len(), 1);
        assert_eq!(bl.payments()[0].cost, 1.5);
    }

    #[test]
    fn recent_lookback_truncates() {
        let mut bl = BorrowLedger::new();
        for i in 0..50 {
            bl.record("J", i as i64, 0.0001 * i as f64, 0.0);
        }
        let r = bl.recent("J", 5);
        assert_eq!(r.len(), 5);
        assert_eq!(r[0].0, 45);
    }
}
