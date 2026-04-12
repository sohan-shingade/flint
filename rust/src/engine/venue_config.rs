//! Per-venue trading configuration (fees, margin, latency, impact).

#[derive(Debug, Clone, Copy)]
pub struct VenueConfig {
    pub taker_fee_bps: f64,
    pub maker_fee_bps: f64,
    pub initial_margin: f64,
    pub maintenance_margin: f64,
    pub max_leverage: f64,
    pub liquidation_penalty: f64,
    pub impact_coefficient: f64,
    pub base_latency_s: f64,
    pub latency_jitter_s: f64,
}

impl VenueConfig {
    pub fn taker_fee_rate(&self) -> f64 {
        self.taker_fee_bps / 10_000.0
    }
    pub fn maker_fee_rate(&self) -> f64 {
        self.maker_fee_bps / 10_000.0
    }
}

/// Lookup venue config by name. Returns default if not found.
pub fn get_venue_config(name: &str) -> VenueConfig {
    match name {
        "drift" => VenueConfig {
            taker_fee_bps: 10.0, maker_fee_bps: -2.0,
            initial_margin: 0.10, maintenance_margin: 0.05, max_leverage: 10.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.01,
            base_latency_s: 8.0, latency_jitter_s: 5.0,
        },
        "hyperliquid" => VenueConfig {
            taker_fee_bps: 3.5, maker_fee_bps: 1.0,
            initial_margin: 0.05, maintenance_margin: 0.025, max_leverage: 20.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.005,
            base_latency_s: 1.0, latency_jitter_s: 0.5,
        },
        "binance" => VenueConfig {
            taker_fee_bps: 4.5, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.002,
            base_latency_s: 0.2, latency_jitter_s: 0.1,
        },
        "okx" => VenueConfig {
            taker_fee_bps: 5.0, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.003,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "bybit" => VenueConfig {
            taker_fee_bps: 5.5, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.003,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "dydx" => VenueConfig {
            taker_fee_bps: 5.0, maker_fee_bps: 1.0,
            initial_margin: 0.05, maintenance_margin: 0.03, max_leverage: 20.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.006,
            base_latency_s: 2.0, latency_jitter_s: 1.0,
        },
        "jupiter" => VenueConfig {
            taker_fee_bps: 6.0, maker_fee_bps: 6.0,
            initial_margin: 0.01, maintenance_margin: 0.002, max_leverage: 100.0,
            liquidation_penalty: 0.0, impact_coefficient: 0.03,
            base_latency_s: 12.0, latency_jitter_s: 8.0,
        },
        "coinbase" => VenueConfig {
            taker_fee_bps: 6.0, maker_fee_bps: 4.0,
            initial_margin: 0.10, maintenance_margin: 0.05, max_leverage: 10.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.003,
            base_latency_s: 0.15, latency_jitter_s: 0.05,
        },
        "kraken" => VenueConfig {
            taker_fee_bps: 4.0, maker_fee_bps: 1.6,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.004,
            base_latency_s: 0.3, latency_jitter_s: 0.1,
        },
        "kucoin" => VenueConfig {
            taker_fee_bps: 6.0, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.004,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "gate" => VenueConfig {
            taker_fee_bps: 7.5, maker_fee_bps: 3.5,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.005,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "bitget" => VenueConfig {
            taker_fee_bps: 6.0, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.003,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "mexc" => VenueConfig {
            taker_fee_bps: 0.0, maker_fee_bps: 0.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.006,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        "htx" => VenueConfig {
            taker_fee_bps: 5.0, maker_fee_bps: 2.0,
            initial_margin: 0.02, maintenance_margin: 0.01, max_leverage: 50.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.005,
            base_latency_s: 0.3, latency_jitter_s: 0.15,
        },
        _ => VenueConfig {
            taker_fee_bps: 5.0, maker_fee_bps: 0.0,
            initial_margin: 0.10, maintenance_margin: 0.05, max_leverage: 10.0,
            liquidation_penalty: 0.01, impact_coefficient: 0.005,
            base_latency_s: 1.0, latency_jitter_s: 0.5,
        },
    }
}
