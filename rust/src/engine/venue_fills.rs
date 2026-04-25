//! Per-venue fill pipelines (Drift, Hyperliquid, Jupiter, CEX).
//!
//! Each pipeline models venue-specific execution mechanics. The [`VenueFiller`]
//! enum unifies them so the runner can dispatch by venue_id.

use crate::types::*;
use super::fills::{self, walk_book};
use super::synthetic_depth::*;
use rand::prelude::*;
use rand_chacha::ChaCha8Rng;

// ---------------------------------------------------------------------------
// Drift 3-tier fill model
// ---------------------------------------------------------------------------

pub struct DriftFillPipeline {
    jit_fill_probability: f64,
    auction_improvement_bps: f64,
    rng: ChaCha8Rng,
    profile: DepthProfile,
}

impl DriftFillPipeline {
    pub fn new(seed: u64) -> Self {
        Self {
            jit_fill_probability: 0.6,
            auction_improvement_bps: 2.0,
            rng: ChaCha8Rng::seed_from_u64(seed),
            profile: get_depth_profile("drift"),
        }
    }

    pub fn fill_market(&mut self, order: &OrderData, candle: &CandleBar) -> FillResult {
        let mut remaining = order.size;
        let mut total_cost = 0.0;

        // Tier 1: JIT Auction
        if self.rng.gen::<f64>() < self.jit_fill_probability {
            let jit_fraction = self.rng.gen_range(0.3..0.8);
            let jit_size = remaining * jit_fraction;
            let improvement = (self.auction_improvement_bps / 10_000.0) * candle.close;
            let jit_price = match order.side {
                Side::Long => candle.close - improvement,
                Side::Short => candle.close + improvement,
            };
            total_cost += jit_size * jit_price;
            remaining -= jit_size;
        }

        // Tier 2: DLOB walk (synthetic)
        if remaining > 0.0 {
            let (bids, asks) = generate_synthetic_book(candle.close, &self.profile, 20);
            let levels = match order.side {
                Side::Long => &asks,
                Side::Short => &bids,
            };
            if let Some((avg, filled)) = walk_book(levels, remaining, order.side, None, None) {
                total_cost += filled * avg;
                remaining -= filled;
            }
        }

        // Tier 3: vAMM backstop — simple sqrt impact
        if remaining > 0.0 {
            let k = 0.01;
            let impact_pct = k * (remaining / candle.volume.max(1.0)).sqrt();
            let vamm_price = match order.side {
                Side::Long => candle.close * (1.0 + impact_pct),
                Side::Short => candle.close * (1.0 - impact_pct),
            };
            total_cost += remaining * vamm_price;
            remaining = 0.0;
        }

        let filled = order.size - remaining;
        let avg_price = if filled > 0.0 { total_cost / filled } else { candle.close };

        FillResult {
            order_id: order.order_id.clone(),
            market_id: order.market_id,
            venue_id: order.venue_id,
            side: order.side,
            price: avg_price,
            size: filled,
            fee: 0.0,
            ts: candle.ts,
            is_partial: false,
            latency_ms: 0.0,
            impact_bps: 0.0,
            tx_cost: 0.0,
            is_maker: false,
        }
    }
}

// ---------------------------------------------------------------------------
// Hyperliquid CLOB + HLP fill model
// ---------------------------------------------------------------------------

pub struct HyperliquidFillPipeline {
    impact_k: f64,
    profile: DepthProfile,
}

impl HyperliquidFillPipeline {
    pub fn new() -> Self {
        Self {
            impact_k: 0.005,
            profile: get_depth_profile("hyperliquid"),
        }
    }

    pub fn fill_market(&self, order: &OrderData, candle: &CandleBar) -> FillResult {
        let (bids, asks) = generate_synthetic_book(candle.close, &self.profile, 20);
        let levels = match order.side {
            Side::Long => &asks,
            Side::Short => &bids,
        };

        let mut remaining = order.size;
        let mut total_cost = 0.0;
        let mut filled = 0.0;

        for level in levels {
            let take = remaining.min(level.size);
            total_cost += take * level.price;
            filled += take;
            remaining -= take;
            if remaining <= 0.0 { break; }
        }

        // HLP vault backstop
        if remaining > 0.0 {
            let pct = self.impact_k * (remaining / candle.volume.max(1.0));
            let hlp_price = match order.side {
                Side::Long => candle.close * (1.0 + pct),
                Side::Short => candle.close * (1.0 - pct),
            };
            total_cost += remaining * hlp_price;
            filled += remaining;
        }

        let avg_price = if filled > 0.0 { total_cost / filled } else { candle.close };

        FillResult {
            order_id: order.order_id.clone(),
            market_id: order.market_id,
            venue_id: order.venue_id,
            side: order.side,
            price: avg_price,
            size: filled,
            fee: 0.0,
            ts: candle.ts,
            is_partial: false,
            latency_ms: 0.0,
            impact_bps: 0.0,
            tx_cost: 0.0,
            is_maker: false,
        }
    }
}

// ---------------------------------------------------------------------------
// Jupiter pool-based + keeper delay fill model
// ---------------------------------------------------------------------------

pub struct JupiterFillPipeline {
    base_latency_s: f64,
    jitter_s: f64,
    rng: ChaCha8Rng,
}

impl JupiterFillPipeline {
    pub fn new(seed: u64) -> Self {
        Self {
            base_latency_s: 12.0,
            jitter_s: 8.0,
            rng: ChaCha8Rng::seed_from_u64(seed),
        }
    }

    pub fn fill_market(
        &mut self,
        order: &OrderData,
        candle: &CandleBar,
        next_candle: Option<&CandleBar>,
    ) -> FillResult {
        let delay = (self.base_latency_s + self.rng.gen_range(-self.jitter_s..self.jitter_s))
            .max(0.0);

        let fill_price_base = self.interpolate_price(candle, delay, next_candle);

        // Quadratic impact
        let notional = order.size * fill_price_base;
        let scalar = 1e9_f64;
        let impact_usd = (notional / scalar) * notional;
        let impact_per_unit = if order.size > 0.0 { impact_usd / order.size } else { 0.0 };

        let fill_price = match order.side {
            Side::Long => fill_price_base + impact_per_unit,
            Side::Short => fill_price_base - impact_per_unit,
        };

        FillResult {
            order_id: order.order_id.clone(),
            market_id: order.market_id,
            venue_id: order.venue_id,
            side: order.side,
            price: fill_price,
            size: order.size,
            fee: 0.0,
            ts: candle.ts,
            is_partial: false,
            latency_ms: delay * 1000.0,
            impact_bps: 0.0,
            tx_cost: 0.0,
            is_maker: false,
        }
    }

    fn interpolate_price(&self, candle: &CandleBar, delay: f64, next: Option<&CandleBar>) -> f64 {
        let bar_dur = candle.resolution_s as f64;
        if bar_dur <= 0.0 { return candle.close; }

        if delay <= bar_dur {
            let frac = delay / bar_dur;
            return candle.open + frac * (candle.close - candle.open);
        }

        if let Some(nc) = next {
            let remaining = delay - bar_dur;
            let frac = (remaining / nc.resolution_s as f64).min(1.0);
            return nc.open + frac * (nc.close - nc.open);
        }

        candle.close
    }
}

// ---------------------------------------------------------------------------
// CEX fill model (Binance, OKX, Coinbase, Kraken, KuCoin, Gate, Bitget, MEXC, HTX)
// ---------------------------------------------------------------------------

const MAX_PARTICIPATION: f64 = 0.10;

pub struct CexFillPipeline {
    pub venue_name: String,
    pub taker_fee_pct: f64,
    pub profile: DepthProfile,
    pub ioc_band_pct: Option<f64>, // Bybit: Some(0.01)
}

impl CexFillPipeline {
    pub fn new(venue: &str) -> Self {
        let profile = get_depth_profile(venue);
        let (taker_fee_bps, ioc_band) = match venue {
            "binance"  => (5.0, None),
            "okx"      => (5.0, None),
            "bybit"    => (5.5, Some(0.01)),
            "coinbase" => (6.0, None),
            "kraken"   => (4.0, None),
            "kucoin"   => (6.0, None),
            "gate"     => (7.5, None),
            "bitget"   => (6.0, None),
            "mexc"     => (0.0, None),
            "htx"      => (5.0, None),
            _          => (5.0, None),
        };
        Self {
            venue_name: venue.to_string(),
            taker_fee_pct: taker_fee_bps / 10_000.0,
            profile,
            ioc_band_pct: ioc_band,
        }
    }

    pub fn fill_market(&self, order: &OrderData, candle: &CandleBar) -> Option<FillResult> {
        let bar_vol_usd = candle.volume * candle.close;
        let scaled = scale_profile(&self.profile, bar_vol_usd);
        let (bids, asks) = generate_synthetic_book(candle.close, &scaled, 20);

        let levels = match order.side {
            Side::Long => &asks,
            Side::Short => &bids,
        };

        let price_cap = self.ioc_band_pct.map(|pct| match order.side {
            Side::Long => candle.close * (1.0 + pct),
            Side::Short => candle.close * (1.0 - pct),
        });

        let max_size = if candle.volume > 0.0 {
            Some(candle.volume * MAX_PARTICIPATION)
        } else {
            None
        };

        let (avg_price, filled) = walk_book(levels, order.size, order.side, price_cap, max_size)?;
        let fee = avg_price * filled * self.taker_fee_pct;

        Some(FillResult {
            order_id: order.order_id.clone(),
            market_id: order.market_id,
            venue_id: order.venue_id,
            side: order.side,
            price: avg_price,
            size: filled,
            fee,
            ts: candle.ts,
            is_partial: filled < order.size,
            latency_ms: 0.0,
            impact_bps: 0.0,
            tx_cost: 0.0,
            is_maker: false,
        })
    }
}

// ---------------------------------------------------------------------------
// Unified venue dispatch enum
// ---------------------------------------------------------------------------

/// Wraps all venue-specific fill pipelines into a single dispatchable type.
pub enum VenueFiller {
    Drift(DriftFillPipeline),
    Hyperliquid(HyperliquidFillPipeline),
    Jupiter(JupiterFillPipeline),
    Cex(CexFillPipeline),
    /// Fallback: generic sqrt impact model.
    Generic { impact_coefficient: f64 },
}

impl VenueFiller {
    /// Create the appropriate filler for a venue name.
    pub fn for_venue(name: &str, seed: u64) -> Self {
        match name {
            "drift" => VenueFiller::Drift(DriftFillPipeline::new(seed)),
            "hyperliquid" => VenueFiller::Hyperliquid(HyperliquidFillPipeline::new()),
            "jupiter" => VenueFiller::Jupiter(JupiterFillPipeline::new(seed)),
            "binance" | "okx" | "bybit" | "coinbase" | "kraken"
            | "kucoin" | "gate" | "bitget" | "mexc" | "htx" => {
                VenueFiller::Cex(CexFillPipeline::new(name))
            }
            _ => VenueFiller::Generic { impact_coefficient: 0.005 },
        }
    }

    /// Execute a market fill using the venue-specific model.
    pub fn fill_market(&mut self, order: &OrderData, candle: &CandleBar) -> FillResult {
        match self {
            VenueFiller::Drift(p) => p.fill_market(order, candle),
            VenueFiller::Hyperliquid(p) => p.fill_market(order, candle),
            VenueFiller::Jupiter(p) => p.fill_market(order, candle, None),
            VenueFiller::Cex(p) => p.fill_market(order, candle)
                .unwrap_or_else(|| Self::generic_fill(order, candle, 0.005)),
            VenueFiller::Generic { impact_coefficient } => {
                Self::generic_fill(order, candle, *impact_coefficient)
            }
        }
    }

    fn generic_fill(order: &OrderData, candle: &CandleBar, k: f64) -> FillResult {
        let impact_bps = fills::sqrt_impact_bps(order.size, candle.volume, k);
        let pct = impact_bps / 10_000.0;
        let price = match order.side {
            Side::Long => candle.close * (1.0 + pct),
            Side::Short => candle.close * (1.0 - pct),
        };
        FillResult {
            order_id: order.order_id.clone(),
            market_id: order.market_id,
            venue_id: order.venue_id,
            side: order.side,
            price,
            size: order.size,
            fee: 0.0,
            ts: candle.ts,
            is_partial: false,
            latency_ms: 0.0,
            impact_bps,
            tx_cost: 0.0,
            is_maker: false,
        }
    }
}
