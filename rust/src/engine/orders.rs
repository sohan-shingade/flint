//! Order matching — stop trigger, limit fill, market order processing.

use crate::types::*;
use super::fills;
use super::fees::FeeModel;

/// Process pending stop/limit orders against a candle. Returns fills.
pub fn process_pending_orders(
    pending: &mut Vec<OrderData>,
    candle: &CandleBar,
    fee_model: &FeeModel,
) -> Vec<FillResult> {
    let mut fills_out = Vec::new();
    let mut remaining = Vec::new();

    for order in pending.drain(..) {
        if order.market_id != candle.market_id {
            remaining.push(order);
            continue;
        }

        let mut fill: Option<FillResult> = None;

        match order.order_type {
            OrderType::StopLoss | OrderType::TakeProfit => {
                if fills::check_stop_trigger(&order, candle) {
                    let price = fills::stop_fill_price(&order, candle);
                    fill = Some(FillResult {
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
                    });
                }
            }
            OrderType::Limit => {
                if let Some(price) = fills::check_limit_fill(&order, candle) {
                    fill = Some(FillResult {
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
                    });
                }
            }
            _ => {}
        }

        if let Some(mut f) = fill {
            f.fee = fee_model.compute_fee(&f);
            fills_out.push(f);
        } else {
            remaining.push(order);
        }
    }

    *pending = remaining;
    fills_out
}

/// Process market orders placed during this bar. Returns fills.
///
/// When `fill_model_type` is `Pipeline`, dispatches to venue-specific fill
/// models via `venue_fillers`. Falls back to generic sqrt impact if no
/// venue filler is registered for the order's venue.
pub fn process_market_orders(
    queue: &mut Vec<OrderData>,
    candle: &CandleBar,
    fee_model: &FeeModel,
    fill_model_type: FillModelType,
    slippage_bps: f64,
    venue_fillers: &mut std::collections::HashMap<VenueId, super::venue_fills::VenueFiller>,
) -> Vec<FillResult> {
    let mut fills_out = Vec::new();

    for order in queue.drain(..) {
        let mut fill = match fill_model_type {
            FillModelType::ClosePrice => fills::fill_close_price(&order, candle),
            FillModelType::NextBarOpen => fills::fill_next_bar_open(&order, candle),
            FillModelType::Slippage => fills::fill_slippage(&order, candle, slippage_bps),
            FillModelType::Pipeline | FillModelType::Orderbook => {
                // Dispatch to venue-specific fill model if available
                if let Some(filler) = venue_fillers.get_mut(&order.venue_id) {
                    filler.fill_market(&order, candle)
                } else {
                    // Fallback: generic sqrt impact
                    let impact_bps = fills::sqrt_impact_bps(order.size, candle.volume, 0.005);
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
                    }
                }
            }
        };

        fill.fee = fee_model.compute_fee(&fill);
        fills_out.push(fill);
    }

    fills_out
}
