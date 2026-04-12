//! Synthetic orderbook generation from per-venue depth profiles.

use crate::types::BookLevel;

#[derive(Debug, Clone, Copy)]
pub struct DepthProfile {
    pub bid_depth_1pct: f64,
    pub ask_depth_1pct: f64,
    pub concentration: f64,
    pub spread_bps: f64,
}

/// Lookup depth profile by venue name.
pub fn get_depth_profile(venue: &str) -> DepthProfile {
    match venue {
        "binance"     => DepthProfile { bid_depth_1pct: 20_000_000.0, ask_depth_1pct: 20_000_000.0, concentration: 0.7, spread_bps: 0.5 },
        "coinbase"    => DepthProfile { bid_depth_1pct: 15_000_000.0, ask_depth_1pct: 15_000_000.0, concentration: 0.65, spread_bps: 0.8 },
        "okx"         => DepthProfile { bid_depth_1pct: 10_000_000.0, ask_depth_1pct: 10_000_000.0, concentration: 0.6, spread_bps: 1.0 },
        "bybit"       => DepthProfile { bid_depth_1pct: 8_000_000.0, ask_depth_1pct: 8_000_000.0, concentration: 0.6, spread_bps: 1.0 },
        "kraken"      => DepthProfile { bid_depth_1pct: 4_000_000.0, ask_depth_1pct: 4_000_000.0, concentration: 0.55, spread_bps: 1.5 },
        "kucoin"      => DepthProfile { bid_depth_1pct: 6_000_000.0, ask_depth_1pct: 6_000_000.0, concentration: 0.55, spread_bps: 1.2 },
        "bitget"      => DepthProfile { bid_depth_1pct: 6_000_000.0, ask_depth_1pct: 6_000_000.0, concentration: 0.55, spread_bps: 1.2 },
        "gate"        => DepthProfile { bid_depth_1pct: 3_000_000.0, ask_depth_1pct: 3_000_000.0, concentration: 0.5, spread_bps: 2.0 },
        "mexc"        => DepthProfile { bid_depth_1pct: 2_000_000.0, ask_depth_1pct: 2_000_000.0, concentration: 0.45, spread_bps: 2.5 },
        "htx"         => DepthProfile { bid_depth_1pct: 3_000_000.0, ask_depth_1pct: 3_000_000.0, concentration: 0.5, spread_bps: 2.0 },
        "hyperliquid" => DepthProfile { bid_depth_1pct: 5_000_000.0, ask_depth_1pct: 5_000_000.0, concentration: 0.5, spread_bps: 1.5 },
        "drift"       => DepthProfile { bid_depth_1pct: 2_000_000.0, ask_depth_1pct: 2_000_000.0, concentration: 0.4, spread_bps: 3.0 },
        _             => DepthProfile { bid_depth_1pct: 5_000_000.0, ask_depth_1pct: 5_000_000.0, concentration: 0.5, spread_bps: 1.5 },
    }
}

/// Generate synthetic orderbook from a depth profile.
pub fn generate_synthetic_book(
    mid_price: f64,
    profile: &DepthProfile,
    levels: usize,
) -> (Vec<BookLevel>, Vec<BookLevel>) {
    let half_spread = (profile.spread_bps / 10_000.0) * mid_price / 2.0;
    let step = (mid_price * 0.01) / levels as f64;

    let decay = 1.0 + profile.concentration * 4.0;
    let weights: Vec<f64> = (0..levels)
        .map(|i| (-decay * i as f64 / levels as f64).exp())
        .collect();
    let total_weight: f64 = weights.iter().sum();

    let mut bids = Vec::with_capacity(levels);
    let mut asks = Vec::with_capacity(levels);

    for i in 0..levels {
        let frac = weights[i] / total_weight;

        let bid_price = mid_price - half_spread - i as f64 * step;
        let bid_usd = profile.bid_depth_1pct * frac;
        let bid_size = if bid_price > 0.0 { bid_usd / bid_price } else { 0.0 };
        bids.push(BookLevel { price: bid_price, size: bid_size });

        let ask_price = mid_price + half_spread + i as f64 * step;
        let ask_usd = profile.ask_depth_1pct * frac;
        let ask_size = if ask_price > 0.0 { ask_usd / ask_price } else { 0.0 };
        asks.push(BookLevel { price: ask_price, size: ask_size });
    }

    (bids, asks)
}

/// Scale a depth profile by bar volume ratio.
pub fn scale_profile(profile: &DepthProfile, bar_volume_usd: f64) -> DepthProfile {
    const VOL_DEPTH_RATIO: f64 = 20.0;
    const FLOOR: f64 = 0.05;
    const CAP: f64 = 3.0;

    if bar_volume_usd <= 0.0 {
        return *profile;
    }

    let expected_vol = profile.bid_depth_1pct * VOL_DEPTH_RATIO;
    let scale = (bar_volume_usd / expected_vol).clamp(FLOOR, CAP);

    DepthProfile {
        bid_depth_1pct: profile.bid_depth_1pct * scale,
        ask_depth_1pct: profile.ask_depth_1pct * scale,
        ..*profile
    }
}
