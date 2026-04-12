//! Performance metrics — Sharpe ratio, max drawdown, per-venue aggregation.

/// Compute max drawdown from equity curve.
pub fn max_drawdown(equity: &[f64]) -> f64 {
    if equity.is_empty() { return 0.0; }
    let mut peak = equity[0];
    let mut max_dd = 0.0;
    for &e in equity {
        if e > peak { peak = e; }
        if peak > 0.0 {
            let dd = (peak - e) / peak;
            if dd > max_dd { max_dd = dd; }
        }
    }
    max_dd
}

/// Compute annualized Sharpe ratio from equity curve.
pub fn sharpe_ratio(equity: &[f64], periods_per_year: f64) -> f64 {
    if equity.len() < 2 { return 0.0; }

    let n = equity.len() - 1;
    let mut returns = Vec::with_capacity(n);
    for i in 0..n {
        if equity[i].abs() < 1e-12 { continue; }
        returns.push((equity[i + 1] - equity[i]) / equity[i]);
    }

    if returns.len() < 2 { return 0.0; }

    let mean: f64 = returns.iter().sum::<f64>() / returns.len() as f64;
    let var: f64 = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>()
        / (returns.len() - 1) as f64;
    let std = var.sqrt();

    if std < 1e-12 { return 0.0; }
    mean / std * periods_per_year.sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_max_drawdown_flat() {
        assert_eq!(max_drawdown(&[100.0, 100.0, 100.0]), 0.0);
    }

    #[test]
    fn test_max_drawdown_simple() {
        let eq = vec![100.0, 110.0, 90.0, 95.0, 80.0, 100.0];
        let dd = max_drawdown(&eq);
        assert!((dd - 30.0 / 110.0).abs() < 0.001);
    }

    #[test]
    fn test_sharpe_positive() {
        let eq = vec![100.0, 101.0, 102.0, 103.0, 104.0];
        assert!(sharpe_ratio(&eq, 8760.0) > 0.0);
    }
}
