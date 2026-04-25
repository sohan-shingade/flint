//! OrderbookFiller — walk a book snapshot to compute a realistic VWAP fill.
//!
//! D-3.1-rust (Wave 2): port of `flint/execution/fill_models.py:OrderbookFillModel`.
//! Matches the Python algorithm one-for-one:
//!   1. Pick asks for LONG, bids for SHORT.
//!   2. Reject when order size exceeds total book depth on the taking side
//!      (when `reject_on_insufficient_depth` is true — the default).
//!   3. Walk levels in order, accumulate `(size × price)`, compute VWAP.
//!   4. Signed impact_bps = (vwap - mid) / mid × 10_000, positive = worse-for-taker.
//!
//! Parity is pinned by `tests/test_rust_orderbook_parity.py` to 1e-9.

use crate::types::Side;

#[derive(Debug, Clone, Copy)]
pub struct BookLevel {
    pub price: f64,
    pub size: f64,
}

#[derive(Debug, Clone)]
pub struct BookSnapshot {
    pub bids: Vec<BookLevel>,   // descending by price (best first)
    pub asks: Vec<BookLevel>,   // ascending by price (best first)
}

impl BookSnapshot {
    /// Best-bid / best-ask midpoint. Returns 0.0 when the book has no
    /// top level on either side.
    pub fn mid(&self) -> f64 {
        let best_bid = self.bids.first().map(|l| l.price).unwrap_or(0.0);
        let best_ask = self.asks.first().map(|l| l.price).unwrap_or(0.0);
        if best_bid > 0.0 && best_ask > 0.0 {
            (best_bid + best_ask) / 2.0
        } else if best_bid > 0.0 {
            best_bid
        } else {
            best_ask
        }
    }
}

/// Outcome of walking the book for a single order. Python returns
/// `Option[Fill]`; here we surface `None` for rejection so the
/// fill-pipeline caller can fall back to SlippageFill identically.
#[derive(Debug, Clone, Copy)]
pub struct OrderbookWalk {
    pub price: f64,         // volume-weighted average fill price
    pub size: f64,          // filled size (may be < order.size on partial)
    pub impact_bps: f64,    // signed vs mid, positive = taker-unfavorable
    pub is_partial: bool,
}

#[derive(Debug, Clone)]
pub struct OrderbookFiller {
    /// When true, reject orders whose size exceeds total book depth on
    /// the taking side. Default matches the Python default (true) so
    /// "I ate the whole book" becomes a loud rejection instead of a
    /// silent underfill.
    pub reject_on_insufficient_depth: bool,
}

impl Default for OrderbookFiller {
    fn default() -> Self {
        Self { reject_on_insufficient_depth: true }
    }
}

impl OrderbookFiller {
    pub fn new(reject_on_insufficient_depth: bool) -> Self {
        Self { reject_on_insufficient_depth }
    }

    /// Walk the book for a market order. Returns `None` to signal that
    /// the caller should fall back (empty book, rejected on depth, or
    /// zero filled — all three surface as `None` in the Python model).
    pub fn walk_market(
        &self,
        side: Side,
        order_size: f64,
        book: &BookSnapshot,
    ) -> Option<OrderbookWalk> {
        let levels = match side {
            Side::Long => &book.asks,
            Side::Short => &book.bids,
        };
        if levels.is_empty() {
            return None;
        }

        let total_depth: f64 = levels.iter().map(|l| l.size).sum();
        if self.reject_on_insufficient_depth && order_size > total_depth {
            return None;
        }

        let mut remaining = order_size;
        let mut total_cost = 0.0;
        let mut filled = 0.0;
        for level in levels {
            let take = remaining.min(level.size);
            total_cost += take * level.price;
            filled += take;
            remaining -= take;
            if remaining <= 0.0 {
                break;
            }
        }

        if filled <= 0.0 {
            return None;
        }

        let avg_price = total_cost / filled;
        let mid = book.mid();
        let impact_bps = if mid > 0.0 {
            match side {
                Side::Long => (avg_price - mid) / mid * 10_000.0,
                Side::Short => (mid - avg_price) / mid * 10_000.0,
            }
        } else {
            0.0
        };

        let is_partial = filled < order_size - 1e-12;
        Some(OrderbookWalk { price: avg_price, size: filled, impact_bps, is_partial })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f64 = 1e-9;

    fn _book(bids: &[(f64, f64)], asks: &[(f64, f64)]) -> BookSnapshot {
        BookSnapshot {
            bids: bids.iter().map(|(p, s)| BookLevel { price: *p, size: *s }).collect(),
            asks: asks.iter().map(|(p, s)| BookLevel { price: *p, size: *s }).collect(),
        }
    }

    #[test]
    fn long_fills_against_ask_side_vwap() {
        // ask: 100 x 5, 101 x 8, 102 x 10 → buy 10 → 5@100 + 5@101 = 1005 / 10 = 100.5
        let book = _book(&[(99.0, 100.0)], &[(100.0, 5.0), (101.0, 8.0), (102.0, 10.0)]);
        let w = OrderbookFiller::default().walk_market(Side::Long, 10.0, &book).unwrap();
        assert!((w.price - 100.5).abs() < EPS);
        assert!((w.size - 10.0).abs() < EPS);
        assert!(!w.is_partial);
    }

    #[test]
    fn short_fills_against_bid_side_vwap() {
        // bid: 99 x 5, 98 x 8 → sell 10 → 5@99 + 5@98 = 985 / 10 = 98.5
        let book = _book(&[(99.0, 5.0), (98.0, 8.0)], &[(100.0, 100.0)]);
        let w = OrderbookFiller::default().walk_market(Side::Short, 10.0, &book).unwrap();
        assert!((w.price - 98.5).abs() < EPS);
    }

    #[test]
    fn rejects_order_larger_than_depth_by_default() {
        let book = _book(&[(99.0, 2.0)], &[(100.0, 2.0), (101.0, 2.0)]);
        // total ask depth = 4, order = 10 → reject
        assert!(OrderbookFiller::default().walk_market(Side::Long, 10.0, &book).is_none());
    }

    #[test]
    fn permits_partial_fill_when_reject_disabled() {
        let book = _book(&[(99.0, 2.0)], &[(100.0, 2.0), (101.0, 2.0)]);
        let filler = OrderbookFiller::new(false);
        let w = filler.walk_market(Side::Long, 10.0, &book).unwrap();
        // 2@100 + 2@101 = 402 / 4 = 100.5, filled = 4 < 10 → partial
        assert!((w.price - 100.5).abs() < EPS);
        assert!((w.size - 4.0).abs() < EPS);
        assert!(w.is_partial);
    }

    #[test]
    fn empty_book_returns_none() {
        let book = _book(&[], &[]);
        assert!(OrderbookFiller::default().walk_market(Side::Long, 1.0, &book).is_none());
    }

    #[test]
    fn long_impact_is_positive_when_fill_above_mid() {
        // bid 99, ask 101 → mid = 100; buy 5 @ 101 → impact = +100 bps
        let book = _book(&[(99.0, 10.0)], &[(101.0, 10.0)]);
        let w = OrderbookFiller::default().walk_market(Side::Long, 5.0, &book).unwrap();
        assert!((w.impact_bps - 100.0).abs() < EPS);
    }

    #[test]
    fn short_impact_is_positive_when_fill_below_mid() {
        let book = _book(&[(99.0, 10.0)], &[(101.0, 10.0)]);
        let w = OrderbookFiller::default().walk_market(Side::Short, 5.0, &book).unwrap();
        // sell 5 @ 99 with mid 100 → (100-99)/100*10000 = 100 bps
        assert!((w.impact_bps - 100.0).abs() < EPS);
    }

    #[test]
    fn mid_falls_back_to_one_side_when_half_empty() {
        let book = _book(&[], &[(100.0, 5.0)]);
        assert!((book.mid() - 100.0).abs() < EPS);
        let book = _book(&[(99.0, 5.0)], &[]);
        assert!((book.mid() - 99.0).abs() < EPS);
    }

    #[test]
    fn mid_is_zero_when_both_sides_empty() {
        let book = _book(&[], &[]);
        assert!(book.mid().abs() < EPS);
    }
}
