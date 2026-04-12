//! Margin engine — per-venue margin enforcement and liquidation.

use std::collections::HashMap;
use crate::types::*;
use super::venue_config::VenueConfig;

#[derive(Debug, Clone)]
pub struct LiquidationEvent {
    pub market_id: MarketId,
    pub venue_id: VenueId,
    pub ts: i64,
    pub side: Side,
    pub size: f64,
    pub entry_price: f64,
    pub liq_price: f64,
    pub mark_price: f64,
    pub loss: f64,
    pub penalty: f64,
}

pub fn compute_liquidation_price(
    entry_price: f64,
    size: f64,
    collateral: f64,
    maintenance_margin: f64,
    is_long: bool,
) -> f64 {
    if size <= 0.0 { return 0.0; }
    let notional = entry_price * size;
    let margin_req = maintenance_margin * notional;
    let buffer = collateral - margin_req;
    if is_long {
        (entry_price - buffer / size).max(0.0)
    } else {
        entry_price + buffer / size
    }
}

pub struct MarginEngine {
    configs: HashMap<VenueId, VenueConfig>,
    pub liquidation_events: Vec<LiquidationEvent>,
}

impl MarginEngine {
    pub fn new(configs: HashMap<VenueId, VenueConfig>) -> Self {
        Self { configs, liquidation_events: Vec::new() }
    }

    pub fn check_can_open(
        &self,
        order: &OrderData,
        available_cash: f64,
        price: f64,
    ) -> (bool, String) {
        let cfg = self.configs.get(&order.venue_id);
        let im = cfg.map_or(0.10, |c| c.initial_margin);
        let notional = order.size * price;
        let required = notional * im;
        if required > available_cash {
            (false, format!("Insufficient margin: need {:.2}, have {:.2}", required, available_cash))
        } else {
            (true, String::new())
        }
    }

    pub fn check_liquidations(
        &mut self,
        positions: &HashMap<(VenueId, MarketId), PositionState>,
        prices: &HashMap<MarketId, f64>,
        cash: f64,
        ts: i64,
    ) -> Vec<LiquidationEvent> {
        let mut events = Vec::new();

        for ((_vid, _mid), pos) in positions {
            let mark = match prices.get(&pos.market_id) {
                Some(&p) => p,
                None => continue,
            };

            let cfg = self.configs.get(&pos.venue_id);
            let mmr = cfg.map_or(0.05, |c| c.maintenance_margin);
            let penalty_rate = cfg.map_or(0.01, |c| c.liquidation_penalty);

            let is_long = pos.side == Side::Long;
            let collateral = cash.max(0.0); // simplified
            let liq = compute_liquidation_price(
                pos.entry_price, pos.size, collateral, mmr, is_long);

            let triggered = if is_long { mark <= liq } else { mark >= liq };
            if triggered {
                let pnl = match pos.side {
                    Side::Long => (mark - pos.entry_price) * pos.size,
                    Side::Short => (pos.entry_price - mark) * pos.size,
                };
                let penalty = pos.size * mark * penalty_rate;
                let loss = pnl - penalty;

                let event = LiquidationEvent {
                    market_id: pos.market_id,
                    venue_id: pos.venue_id,
                    ts,
                    side: pos.side,
                    size: pos.size,
                    entry_price: pos.entry_price,
                    liq_price: liq,
                    mark_price: mark,
                    loss,
                    penalty,
                };
                events.push(event);
            }
        }

        self.liquidation_events.extend(events.clone());
        events
    }
}
