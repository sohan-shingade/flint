//! FundingLedger — Rust port of `flint/execution/funding_ledger.py`.
//!
//! D-6.4-replay (Wave 5 Rust ledgers): per-market and per-venue
//! funding-rate history with O(1) latest + O(lookback) recent
//! reads. Kept tight on purpose — the Python class is the canonical
//! shape and parity tests pin every method's output to 1e-9.

use std::collections::HashMap;

#[derive(Debug, Clone, Copy)]
pub struct FundingPoint {
    pub ts: i64,
    pub rate: f64,
}

#[derive(Debug, Default, Clone)]
pub struct FundingLedger {
    /// market → flat history (used by `latest`, `recent`).
    history: HashMap<String, Vec<FundingPoint>>,
    /// market → venue → split history (used by `by_venue`).
    venue_history: HashMap<String, HashMap<String, Vec<FundingPoint>>>,
}

impl FundingLedger {
    pub fn new() -> Self {
        Self {
            history: HashMap::new(),
            venue_history: HashMap::new(),
        }
    }

    pub fn add(&mut self, market: &str, venue: &str, ts: i64, rate: f64) {
        let pt = FundingPoint { ts, rate };
        self.history
            .entry(market.to_string())
            .or_default()
            .push(pt);
        self.venue_history
            .entry(market.to_string())
            .or_default()
            .entry(venue.to_string())
            .or_default()
            .push(pt);
    }

    pub fn latest(&self, market: &str) -> Option<f64> {
        self.history
            .get(market)
            .and_then(|h| h.last())
            .map(|p| p.rate)
    }

    pub fn recent(&self, market: &str, lookback: usize) -> Vec<(i64, f64)> {
        let history = match self.history.get(market) {
            Some(h) => h,
            None => return Vec::new(),
        };
        let start = history.len().saturating_sub(lookback);
        history[start..]
            .iter()
            .map(|p| (p.ts, p.rate))
            .collect()
    }

    pub fn by_venue(
        &self,
        market: &str,
        lookback: usize,
    ) -> HashMap<String, Vec<(i64, f64)>> {
        let mut out = HashMap::new();
        let venues = match self.venue_history.get(market) {
            Some(v) => v,
            None => return out,
        };
        for (venue, points) in venues {
            let start = points.len().saturating_sub(lookback);
            let trimmed: Vec<(i64, f64)> = points[start..]
                .iter()
                .map(|p| (p.ts, p.rate))
                .collect();
            out.insert(venue.clone(), trimmed);
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn latest_and_recent_basic() {
        let mut fl = FundingLedger::new();
        assert!(fl.latest("SOL-PERP").is_none());
        fl.add("SOL-PERP", "drift", 1, 0.0001);
        fl.add("SOL-PERP", "drift", 2, 0.0002);
        assert_eq!(fl.latest("SOL-PERP"), Some(0.0002));
        assert_eq!(
            fl.recent("SOL-PERP", 5),
            vec![(1, 0.0001), (2, 0.0002)]
        );
    }

    #[test]
    fn lookback_truncates() {
        let mut fl = FundingLedger::new();
        for i in 0..10 {
            fl.add("SOL-PERP", "drift", i, 0.0001 * i as f64);
        }
        let r = fl.recent("SOL-PERP", 3);
        assert_eq!(r.len(), 3);
        assert_eq!(r[0].0, 7);
    }

    #[test]
    fn by_venue_groups_correctly() {
        let mut fl = FundingLedger::new();
        fl.add("SOL-PERP", "drift", 1, 0.0001);
        fl.add("SOL-PERP", "hyperliquid", 1, 0.0002);
        fl.add("SOL-PERP", "drift", 2, 0.0003);
        let bv = fl.by_venue("SOL-PERP", 10);
        assert_eq!(bv.len(), 2);
        assert_eq!(bv["drift"], vec![(1, 0.0001), (2, 0.0003)]);
        assert_eq!(bv["hyperliquid"], vec![(1, 0.0002)]);
    }

    #[test]
    fn unknown_market_returns_empty() {
        let fl = FundingLedger::new();
        assert!(fl.recent("MISSING", 10).is_empty());
        assert!(fl.by_venue("MISSING", 10).is_empty());
    }
}
