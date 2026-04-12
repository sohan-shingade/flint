//! Fee models — compute trade fees from fills.

use crate::types::{FillResult, Side};

#[derive(Debug, Clone, Copy)]
pub enum FeeModel {
    Flat { fee_bps: f64 },
    DriftTiered { maker_fee: f64, taker_fee: f64 },
    Zero,
}

impl FeeModel {
    pub fn compute_fee(&self, fill: &FillResult) -> f64 {
        match self {
            FeeModel::Flat { fee_bps } => {
                fill.size.abs() * fill.price * fee_bps / 10_000.0
            }
            FeeModel::DriftTiered { taker_fee, .. } => {
                fill.size.abs() * fill.price * taker_fee
            }
            FeeModel::Zero => 0.0,
        }
    }
}
