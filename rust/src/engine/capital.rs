//! Per-venue capital allocation with transfer delays.

use std::collections::HashMap;
use crate::types::VenueId;

#[derive(Debug, Clone)]
pub struct Transfer {
    pub from_venue: VenueId,
    pub to_venue: VenueId,
    pub amount: f64,
    pub cost: f64,
    pub initiated_ts: i64,
    pub arrival_ts: i64,
}

pub struct VenueAllocator {
    balances: HashMap<VenueId, f64>,
    in_transit: Vec<Transfer>,
    completed: Vec<Transfer>,
    venue_pnl: HashMap<VenueId, f64>,
    default_transfer_time_s: i64,
    default_transfer_cost: f64,
}

impl VenueAllocator {
    pub fn new(initial: HashMap<VenueId, f64>) -> Self {
        let pnl = initial.keys().map(|&k| (k, 0.0)).collect();
        Self {
            balances: initial,
            in_transit: Vec::new(),
            completed: Vec::new(),
            venue_pnl: pnl,
            default_transfer_time_s: 1800,
            default_transfer_cost: 1.0,
        }
    }

    pub fn total_cash(&self) -> f64 {
        let bal: f64 = self.balances.values().sum();
        let transit: f64 = self.in_transit.iter().map(|t| t.amount).sum();
        bal + transit
    }

    pub fn available(&self, venue: VenueId) -> f64 {
        self.balances.get(&venue).copied().unwrap_or(0.0)
    }

    pub fn debit(&mut self, venue: VenueId, amount: f64) -> bool {
        let bal = self.balances.get_mut(&venue);
        match bal {
            Some(b) if *b >= amount => { *b -= amount; true }
            _ => false,
        }
    }

    pub fn credit(&mut self, venue: VenueId, amount: f64) {
        *self.balances.entry(venue).or_insert(0.0) += amount;
    }

    pub fn track_pnl(&mut self, venue: VenueId, pnl: f64) {
        *self.venue_pnl.entry(venue).or_insert(0.0) += pnl;
    }

    pub fn transfer(&mut self, from: VenueId, to: VenueId, amount: f64, ts: i64) -> Option<Transfer> {
        let total = amount + self.default_transfer_cost;
        let bal = self.balances.get(&from).copied().unwrap_or(0.0);
        if bal < total { return None; }

        *self.balances.get_mut(&from).unwrap() -= total;
        let t = Transfer {
            from_venue: from,
            to_venue: to,
            amount,
            cost: self.default_transfer_cost,
            initiated_ts: ts,
            arrival_ts: ts + self.default_transfer_time_s,
        };
        self.in_transit.push(t.clone());
        Some(t)
    }

    pub fn process_arrivals(&mut self, current_ts: i64) -> Vec<Transfer> {
        let mut arrived = Vec::new();
        let mut remaining = Vec::new();

        for t in self.in_transit.drain(..) {
            if current_ts >= t.arrival_ts {
                *self.balances.entry(t.to_venue).or_insert(0.0) += t.amount;
                arrived.push(t.clone());
                self.completed.push(t);
            } else {
                remaining.push(t);
            }
        }

        self.in_transit = remaining;
        arrived
    }

    pub fn balances(&self) -> &HashMap<VenueId, f64> {
        &self.balances
    }
}
