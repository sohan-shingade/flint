//! Generic fill models (close, next-bar-open, slippage, orderbook, pipeline).

use crate::types::*;
use super::synthetic_depth::{self, DepthProfile, generate_synthetic_book, scale_profile};

/// Walk orderbook levels and return (avg_price, filled_size).
pub fn walk_book(
    levels: &[BookLevel],
    order_size: f64,
    side: Side,
    price_cap: Option<f64>,
    max_size: Option<f64>,
) -> Option<(f64, f64)> {
    let remaining_start = match max_size {
        Some(ms) => order_size.min(ms),
        None => order_size,
    };
    let mut remaining = remaining_start;
    let mut total_cost = 0.0;
    let mut filled = 0.0;

    for level in levels {
        if let Some(cap) = price_cap {
            match side {
                Side::Long if level.price > cap => break,
                Side::Short if level.price < cap => break,
                _ => {}
            }
        }
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
    Some((total_cost / filled, filled))
}

/// Fill at candle close price.
pub fn fill_close_price(order: &OrderData, candle: &CandleBar) -> FillResult {
    FillResult {
        order_id: order.order_id.clone(),
        market_id: order.market_id,
        venue_id: order.venue_id,
        side: order.side,
        price: candle.close,
        size: order.size,
        fee: 0.0,
        ts: candle.ts,
        is_partial: false,
        latency_ms: 0.0,
        impact_bps: 0.0,
        tx_cost: 0.0,
    }
}

/// Fill at candle open price (next-bar-open model).
pub fn fill_next_bar_open(order: &OrderData, candle: &CandleBar) -> FillResult {
    FillResult {
        order_id: order.order_id.clone(),
        market_id: order.market_id,
        venue_id: order.venue_id,
        side: order.side,
        price: candle.open,
        size: order.size,
        fee: 0.0,
        ts: candle.ts,
        is_partial: false,
        latency_ms: 0.0,
        impact_bps: 0.0,
        tx_cost: 0.0,
    }
}

/// Fill with basis-point slippage.
pub fn fill_slippage(order: &OrderData, candle: &CandleBar, slippage_bps: f64) -> FillResult {
    let pct = slippage_bps / 10_000.0;
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
        impact_bps: 0.0,
        tx_cost: 0.0,
    }
}

/// Fill by walking orderbook levels.
pub fn fill_orderbook(
    order: &OrderData,
    candle: &CandleBar,
    book: Option<&OrderbookData>,
    fallback_bps: f64,
) -> FillResult {
    if let Some(book) = book {
        let levels = match order.side {
            Side::Long => &book.asks,
            Side::Short => &book.bids,
        };
        if let Some((avg_price, filled)) = walk_book(levels, order.size, order.side, None, None) {
            return FillResult {
                order_id: order.order_id.clone(),
                market_id: order.market_id,
                venue_id: order.venue_id,
                side: order.side,
                price: avg_price,
                size: filled,
                fee: 0.0,
                ts: candle.ts,
                is_partial: filled < order.size,
                latency_ms: 0.0,
                impact_bps: 0.0,
                tx_cost: 0.0,
            };
        }
    }
    // Fallback to slippage
    fill_slippage(order, candle, fallback_bps)
}

/// Check if a limit order fills on this candle.
pub fn check_limit_fill(order: &OrderData, candle: &CandleBar) -> Option<f64> {
    match order.side {
        Side::Long if candle.low <= order.price => Some(order.price),
        Side::Short if candle.high >= order.price => Some(order.price),
        _ => None,
    }
}

/// Check if a stop order triggers on this candle.
pub fn check_stop_trigger(order: &OrderData, candle: &CandleBar) -> bool {
    match order.order_type {
        OrderType::StopLoss => match order.side {
            Side::Short => candle.low <= order.price,   // long stop
            Side::Long => candle.high >= order.price,    // short stop
        },
        OrderType::TakeProfit => match order.side {
            Side::Short => candle.high >= order.price,   // long TP
            Side::Long => candle.low <= order.price,      // short TP
        },
        _ => false,
    }
}

/// Compute stop fill price (slippage through).
pub fn stop_fill_price(order: &OrderData, candle: &CandleBar) -> f64 {
    match order.order_type {
        OrderType::StopLoss => match order.side {
            Side::Short => order.price.min(candle.close), // long stop: worse of trigger/close
            Side::Long => order.price.max(candle.close),   // short stop
        },
        OrderType::TakeProfit => match order.side {
            Side::Short => order.price.max(candle.close), // long TP: better of trigger/close
            Side::Long => order.price.min(candle.close),
        },
        _ => candle.close,
    }
}

/// Sqrt participation impact model.
pub fn sqrt_impact_bps(order_size: f64, candle_volume: f64, coefficient: f64) -> f64 {
    if candle_volume <= 0.0 {
        return 0.0;
    }
    let participation = order_size / candle_volume;
    coefficient * participation.sqrt() * 10_000.0
}
