"""
Flint Research Script: Cross-Market Analysis for SOL-PERP, BTC-PERP, ETH-PERP
Analyzes: returns, volatility, correlations, regimes, funding rates,
          mean reversion, momentum, volume patterns, and alpha potential.
"""

import requests
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone

BASE = "http://localhost:8000/api/v1"
MARKETS = ["SOL-PERP", "BTC-PERP", "ETH-PERP"]
START_TS = 1735689600   # 2025-01-01 00:00:00 UTC
END_TS   = 1742947200   # 2025-03-26 00:00:00 UTC
HOURS_PER_DAY = 24

# ── Fetch data ──────────────────────────────────────────────────────────────

def fetch_ohlcv(market: str) -> list[dict]:
    r = requests.get(f"{BASE}/data/ohlcv", params={
        "market": market, "resolution": 3600,
        "start_ts": START_TS, "end_ts": END_TS,
        "limit": 5000,
    })
    r.raise_for_status()
    return r.json()["candles"]

def fetch_funding(market: str) -> dict[str, list[dict]]:
    r = requests.get(f"{BASE}/data/funding", params={
        "market": market, "start_ts": START_TS, "end_ts": END_TS
    })
    r.raise_for_status()
    return r.json()["venues"]


print("Fetching data...")
candles = {}
funding = {}
for m in MARKETS:
    candles[m] = fetch_ohlcv(m)
    funding[m] = fetch_funding(m)
    print(f"  {m}: {len(candles[m])} candles, funding venues: {list(funding[m].keys())}")

# ── Helper: extract arrays ──────────────────────────────────────────────────

def to_arrays(clist):
    """Return dict of numpy arrays from candle list."""
    ts = np.array([c["ts"] for c in clist], dtype=np.float64)
    o  = np.array([c["open"] for c in clist], dtype=np.float64)
    h  = np.array([c["high"] for c in clist], dtype=np.float64)
    l  = np.array([c["low"] for c in clist], dtype=np.float64)
    c  = np.array([c["close"] for c in clist], dtype=np.float64)
    v  = np.array([c_["volume"] for c_ in clist], dtype=np.float64)
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}

arrays = {m: to_arrays(candles[m]) for m in MARKETS}

# ── 1. Daily returns and volatility ────────────────────────────────────────

def hourly_returns(close):
    return np.diff(close) / close[:-1]

def daily_close(close, n_hours=HOURS_PER_DAY):
    """Resample hourly closes to daily by taking every 24th bar."""
    n_days = len(close) // n_hours
    return close[:n_days * n_hours].reshape(n_days, n_hours)[:, -1]

def daily_returns(close):
    dc = daily_close(close)
    return np.diff(dc) / dc[:-1]

print("\n" + "=" * 80)
print("1. DAILY RETURNS & VOLATILITY")
print("=" * 80)

stats = {}
for m in MARKETS:
    dr = daily_returns(arrays[m]["close"])
    hr = hourly_returns(arrays[m]["close"])
    ann_vol = np.std(dr) * np.sqrt(365)
    ann_ret = np.mean(dr) * 365
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    max_daily = np.max(dr)
    min_daily = np.min(dr)
    # Max drawdown from daily closes
    dc = daily_close(arrays[m]["close"])
    peak = np.maximum.accumulate(dc)
    dd = (dc - peak) / peak
    max_dd = np.min(dd)

    stats[m] = {
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "best_day": max_daily,
        "worst_day": min_daily,
        "n_days": len(dr),
        "daily_returns": dr,
        "hourly_returns": hr,
    }
    print(f"\n  {m}:")
    print(f"    Annualized Return:   {ann_ret:+.1%}")
    print(f"    Annualized Vol:      {ann_vol:.1%}")
    print(f"    Sharpe Ratio:        {sharpe:+.2f}")
    print(f"    Max Drawdown:        {max_dd:.1%}")
    print(f"    Best Day:            {max_daily:+.2%}")
    print(f"    Worst Day:           {min_daily:+.2%}")
    print(f"    Days analyzed:       {len(dr)}")

# ── 2. Correlation matrix ──────────────────────────────────────────────────

print("\n" + "=" * 80)
print("2. CORRELATION MATRIX (daily returns)")
print("=" * 80)

# Align daily returns to shortest series
min_len = min(len(stats[m]["daily_returns"]) for m in MARKETS)
dr_matrix = np.column_stack([stats[m]["daily_returns"][:min_len] for m in MARKETS])
corr = np.corrcoef(dr_matrix.T)

header = f"{'':>12}" + "".join(f"{m:>12}" for m in MARKETS)
print(f"\n{header}")
for i, m in enumerate(MARKETS):
    row = f"{m:>12}" + "".join(f"{corr[i,j]:>12.3f}" for j in range(len(MARKETS)))
    print(row)

# Hourly correlation too
min_hlen = min(len(stats[m]["hourly_returns"]) for m in MARKETS)
hr_matrix = np.column_stack([stats[m]["hourly_returns"][:min_hlen] for m in MARKETS])
corr_h = np.corrcoef(hr_matrix.T)

print("\n  Hourly return correlations:")
for i, m1 in enumerate(MARKETS):
    for j, m2 in enumerate(MARKETS):
        if j > i:
            print(f"    {m1} vs {m2}: {corr_h[i,j]:.3f}")

# ── 3. Regime detection ───────────────────────────────────────────────────

print("\n" + "=" * 80)
print("3. REGIME DETECTION (20-day SMA slope)")
print("=" * 80)

def sma(arr, window):
    """Simple moving average."""
    out = np.full_like(arr, np.nan)
    for i in range(window - 1, len(arr)):
        out[i] = np.mean(arr[i - window + 1:i + 1])
    return out

def detect_regimes(close, sma_window=20):
    """Classify each day as bull/bear/sideways based on SMA slope."""
    dc = daily_close(close)
    sma20 = sma(dc, sma_window)
    # Slope = (SMA today - SMA yesterday) / SMA yesterday
    slopes = np.diff(sma20) / sma20[:-1]
    # Thresholds: >0.3% daily slope = bull, <-0.3% = bear, else sideways
    threshold = 0.003
    regimes = []
    for s in slopes:
        if np.isnan(s):
            regimes.append("unknown")
        elif s > threshold:
            regimes.append("bull")
        elif s < -threshold:
            regimes.append("bear")
        else:
            regimes.append("sideways")
    return regimes, slopes

for m in MARKETS:
    regimes, slopes = detect_regimes(arrays[m]["close"])
    valid = [r for r in regimes if r != "unknown"]
    counts = {r: valid.count(r) for r in ["bull", "bear", "sideways"]}
    total = len(valid)
    print(f"\n  {m} ({total} days analyzed):")
    for r in ["bull", "bear", "sideways"]:
        pct = counts[r] / total * 100 if total > 0 else 0
        print(f"    {r:>8}: {counts[r]:>3} days ({pct:5.1f}%)")

    # Avg daily return in each regime
    dr = stats[m]["daily_returns"]
    # regimes and dr may differ in length; align
    n = min(len(regimes), len(dr))
    for r in ["bull", "bear", "sideways"]:
        idx = [i for i in range(n) if regimes[i] == r]
        if idx:
            avg = np.mean(dr[idx])
            print(f"      avg daily return in {r}: {avg:+.3%}")

# ── 4. Funding rate analysis ──────────────────────────────────────────────

print("\n" + "=" * 80)
print("4. FUNDING RATE ANALYSIS")
print("=" * 80)

for m in MARKETS:
    print(f"\n  {m}:")
    venue_stats = {}
    for venue, records in funding[m].items():
        rates = np.array([r["rate"] for r in records], dtype=np.float64)
        if len(rates) == 0:
            continue
        # Funding rates are hourly; annualize by *8760
        avg_rate = np.mean(rates)
        median_rate = np.median(rates)
        std_rate = np.std(rates)
        ann_rate = avg_rate * 8760  # hourly -> annual
        positive_pct = np.sum(rates > 0) / len(rates) * 100
        venue_stats[venue] = {
            "avg": avg_rate, "median": median_rate, "std": std_rate,
            "ann": ann_rate, "pos_pct": positive_pct, "n": len(rates),
            "rates": rates, "records": records,
        }
        print(f"    {venue:>14}: avg={avg_rate:+.6f}/hr  ann={ann_rate:+.1%}  "
              f"positive={positive_pct:.0f}%  n={len(rates)}")

# Funding rate vs next-day price move correlation
print("\n  Funding rate vs next-day return correlation:")
for m in MARKETS:
    hr_close = arrays[m]["close"]
    for venue, records in funding[m].items():
        # Build a map ts -> rate
        rate_by_ts = {r["ts"]: r["rate"] for r in records}
        ts_arr = arrays[m]["ts"]
        # For each bar, get funding rate and next-bar return
        fund_rates = []
        next_returns = []
        for i in range(len(ts_arr) - 1):
            t = int(ts_arr[i])
            if t in rate_by_ts:
                fund_rates.append(rate_by_ts[t])
                next_returns.append((hr_close[i+1] - hr_close[i]) / hr_close[i])
        if len(fund_rates) > 50:
            corr_val = np.corrcoef(fund_rates, next_returns)[0, 1]
            print(f"    {m} / {venue:>14}: r = {corr_val:+.4f}  (n={len(fund_rates)})")

# Cross-venue spread analysis
print("\n  Cross-venue funding spreads (annualized):")
for m in MARKETS:
    venues_with_data = [(v, funding[m][v]) for v in funding[m] if len(funding[m][v]) > 100]
    if len(venues_with_data) < 2:
        print(f"    {m}: insufficient multi-venue data")
        continue
    # Build rate time-series aligned by timestamp
    all_ts = set()
    venue_rate_map = {}
    for v, recs in venues_with_data:
        venue_rate_map[v] = {r["ts"]: r["rate"] for r in recs}
        all_ts.update(venue_rate_map[v].keys())
    common_ts = sorted(all_ts)
    # Pairwise spread analysis
    vnames = [v for v, _ in venues_with_data]
    for i in range(len(vnames)):
        for j in range(i+1, len(vnames)):
            v1, v2 = vnames[i], vnames[j]
            spreads = []
            for t in common_ts:
                if t in venue_rate_map[v1] and t in venue_rate_map[v2]:
                    spreads.append(venue_rate_map[v1][t] - venue_rate_map[v2][t])
            if len(spreads) > 100:
                sp = np.array(spreads)
                avg_spread = np.mean(np.abs(sp)) * 8760
                print(f"    {m}: {v1} vs {v2}: avg|spread|={avg_spread:.2%}/yr  "
                      f"max={np.max(np.abs(sp))*8760:.1%}/yr  n={len(spreads)}")

# ── 5. Mean reversion: RSI oversold/overbought accuracy ───────────────────

print("\n" + "=" * 80)
print("5. MEAN REVERSION SIGNALS (RSI)")
print("=" * 80)

def compute_rsi(close, period=14):
    """RSI on close prices."""
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    # Exponential moving average
    avg_gain = np.zeros(len(deltas))
    avg_loss = np.zeros(len(deltas))
    avg_gain[period-1] = np.mean(gains[:period])
    avg_loss[period-1] = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gains[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + losses[i]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Prepend NaN for the diff offset
    return np.concatenate([[np.nan], rsi])

for m in MARKETS:
    close = arrays[m]["close"]
    rsi = compute_rsi(close, 14)

    # Test oversold (<30) and overbought (>70) on hourly data
    # Look at returns over next 4, 12, 24 hours
    for label, threshold, direction, compare in [
        ("Oversold (<30)", 30, "long", lambda r: r < threshold),
        ("Overbought (>70)", 70, "short", lambda r: r > threshold),
    ]:
        for lookahead in [4, 12, 24]:
            signals = []
            wins = 0
            total = 0
            returns = []
            for i in range(len(rsi) - lookahead):
                if np.isnan(rsi[i]):
                    continue
                if (label.startswith("Oversold") and rsi[i] < 30) or \
                   (label.startswith("Overbought") and rsi[i] > 70):
                    ret = (close[i + lookahead] - close[i]) / close[i]
                    returns.append(ret)
                    if label.startswith("Oversold") and ret > 0:
                        wins += 1
                    elif label.startswith("Overbought") and ret < 0:
                        wins += 1
                    total += 1
            if total > 0:
                win_rate = wins / total * 100
                avg_ret = np.mean(returns) * 100
                med_ret = np.median(returns) * 100
                signals.append((lookahead, total, win_rate, avg_ret, med_ret))

            if signals:
                for la, n, wr, ar, mr in signals:
                    pass  # collected below

    # Print consolidated
    print(f"\n  {m}:")
    rsi_vals = rsi[~np.isnan(rsi)]
    print(f"    RSI range: {np.min(rsi_vals):.1f} - {np.max(rsi_vals):.1f}, "
          f"mean: {np.mean(rsi_vals):.1f}")

    for label, threshold_val, direction in [
        ("Oversold (<30)", 30, "long"),
        ("Overbought (>70)", 70, "short"),
    ]:
        print(f"    {label} -> go {direction}:")
        for lookahead in [4, 12, 24]:
            wins = 0
            total = 0
            rets = []
            for i in range(len(rsi) - lookahead):
                if np.isnan(rsi[i]):
                    continue
                if (direction == "long" and rsi[i] < threshold_val) or \
                   (direction == "short" and rsi[i] > threshold_val):
                    ret = (close[i + lookahead] - close[i]) / close[i]
                    rets.append(ret)
                    if (direction == "long" and ret > 0) or \
                       (direction == "short" and ret < 0):
                        wins += 1
                    total += 1
            if total > 0:
                wr = wins / total * 100
                avg_r = np.mean(rets)
                med_r = np.median(rets)
                # Edge = avg return if we took every signal
                print(f"      {lookahead:>2}h: signals={total:>4}  win_rate={wr:5.1f}%  "
                      f"avg_ret={avg_r:+.3%}  med_ret={med_r:+.3%}")
            else:
                print(f"      {lookahead:>2}h: no signals")

# ── 6. Momentum: Bollinger Band breakout accuracy ─────────────────────────

print("\n" + "=" * 80)
print("6. MOMENTUM SIGNALS (Bollinger Band breakouts)")
print("=" * 80)

def bollinger_bands(close, window=20, num_std=2.0):
    """Return middle, upper, lower bands."""
    mid = sma(close, window)
    std = np.full_like(close, np.nan)
    for i in range(window - 1, len(close)):
        std[i] = np.std(close[i - window + 1:i + 1])
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower

for m in MARKETS:
    close = arrays[m]["close"]
    mid, upper, lower = bollinger_bands(close, 20, 2.0)

    print(f"\n  {m}:")
    for label, condition_fn, direction in [
        ("Upper band breakout (momentum long)", lambda i: close[i] > upper[i] and not np.isnan(upper[i]), "long"),
        ("Lower band breakout (momentum short)", lambda i: close[i] < lower[i] and not np.isnan(lower[i]), "short"),
    ]:
        print(f"    {label}:")
        for lookahead in [4, 12, 24]:
            wins = 0
            total = 0
            rets = []
            for i in range(len(close) - lookahead):
                if condition_fn(i):
                    ret = (close[i + lookahead] - close[i]) / close[i]
                    rets.append(ret)
                    if (direction == "long" and ret > 0) or \
                       (direction == "short" and ret < 0):
                        wins += 1
                    total += 1
            if total > 0:
                wr = wins / total * 100
                avg_r = np.mean(rets)
                med_r = np.median(rets)
                print(f"      {lookahead:>2}h: signals={total:>4}  win_rate={wr:5.1f}%  "
                      f"avg_ret={avg_r:+.3%}  med_ret={med_r:+.3%}")
            else:
                print(f"      {lookahead:>2}h: no signals")

    # Mean reversion after BB touch (contrarian)
    print(f"    Contrarian: buy at lower band, sell at upper band:")
    for lookahead in [4, 12, 24]:
        # Buy at lower band
        wins_buy = 0
        total_buy = 0
        rets_buy = []
        for i in range(len(close) - lookahead):
            if not np.isnan(lower[i]) and close[i] < lower[i]:
                ret = (close[i + lookahead] - close[i]) / close[i]
                rets_buy.append(ret)
                if ret > 0:
                    wins_buy += 1
                total_buy += 1
        # Sell at upper band
        wins_sell = 0
        total_sell = 0
        rets_sell = []
        for i in range(len(close) - lookahead):
            if not np.isnan(upper[i]) and close[i] > upper[i]:
                ret = (close[i + lookahead] - close[i]) / close[i]
                rets_sell.append(ret)
                if ret < 0:
                    wins_sell += 1
                total_sell += 1
        total = total_buy + total_sell
        if total > 0:
            combined_wr = (wins_buy + wins_sell) / total * 100
            all_rets = rets_buy + [-r for r in rets_sell]  # flip sell returns
            avg_r = np.mean(all_rets)
            print(f"      {lookahead:>2}h: buy_signals={total_buy}  sell_signals={total_sell}  "
                  f"combined_win_rate={combined_wr:5.1f}%  avg_edge={avg_r:+.3%}")

# ── 7. Volume patterns ───────────────────────────────────────────────────

print("\n" + "=" * 80)
print("7. VOLUME PATTERNS")
print("=" * 80)

for m in MARKETS:
    close = arrays[m]["close"]
    volume = arrays[m]["volume"]
    hr_ret = hourly_returns(close)

    # Volume quintiles
    vol_valid = volume[1:]  # align with returns
    min_len_v = min(len(vol_valid), len(hr_ret))
    vol_valid = vol_valid[:min_len_v]
    hr_ret_v = hr_ret[:min_len_v]

    # Sort into quintiles
    quintile_edges = np.percentile(vol_valid, [20, 40, 60, 80])
    quintile_labels = ["Q1 (lowest)", "Q2", "Q3", "Q4", "Q5 (highest)"]

    print(f"\n  {m}:")
    print(f"    Volume quintile -> next-bar return statistics:")

    for q in range(5):
        if q == 0:
            mask = vol_valid <= quintile_edges[0]
        elif q == 4:
            mask = vol_valid > quintile_edges[3]
        else:
            mask = (vol_valid > quintile_edges[q-1]) & (vol_valid <= quintile_edges[q])

        q_rets = hr_ret_v[mask]
        if len(q_rets) > 0:
            # Next bar return (shift by 1 more)
            # Actually, vol_valid[i] is bar i+1's volume, hr_ret_v[i] is return from bar i to i+1
            # We want: given this bar's volume, what's next bar's return
            # So we need to shift returns by 1
            pass

    # Better approach: volume at bar i, return from bar i+1 to i+2
    # volume[i], next_return = (close[i+2] - close[i+1]) / close[i+1]
    vol_signal = volume[:-2]  # volume at bar i
    next_ret = (close[2:] - close[1:-1]) / close[1:-1]  # return from i+1 to i+2
    min_l = min(len(vol_signal), len(next_ret))
    vol_signal = vol_signal[:min_l]
    next_ret = next_ret[:min_l]

    quintile_edges = np.percentile(vol_signal, [20, 40, 60, 80])
    for q in range(5):
        if q == 0:
            mask = vol_signal <= quintile_edges[0]
        elif q == 4:
            mask = vol_signal > quintile_edges[3]
        else:
            mask = (vol_signal > quintile_edges[q-1]) & (vol_signal <= quintile_edges[q])

        q_rets = next_ret[mask]
        if len(q_rets) > 0:
            avg = np.mean(q_rets)
            std = np.std(q_rets)
            abs_avg = np.mean(np.abs(q_rets))
            print(f"      {quintile_labels[q]:>15}: n={len(q_rets):>4}  "
                  f"avg_next_ret={avg:+.4%}  |avg|={abs_avg:.4%}  std={std:.4%}")

    # Volume spike -> next 24h absolute return
    vol_mean = np.mean(volume)
    vol_std_val = np.std(volume)
    spike_threshold = vol_mean + 2 * vol_std_val
    spike_indices = np.where(volume[:-24] > spike_threshold)[0]
    if len(spike_indices) > 0:
        spike_rets = []
        for idx in spike_indices:
            if idx + 24 < len(close):
                ret = (close[idx + 24] - close[idx]) / close[idx]
                spike_rets.append(ret)
        if spike_rets:
            sr = np.array(spike_rets)
            print(f"    Volume spikes (>2 std): n={len(sr)}")
            print(f"      avg 24h return: {np.mean(sr):+.3%}")
            print(f"      avg |24h return|: {np.mean(np.abs(sr)):.3%}")
            print(f"      positive %: {np.sum(sr > 0)/len(sr)*100:.0f}%")

    # Volume-price divergence (price up but volume declining)
    # Use 24-bar rolling averages
    if len(close) > 48:
        vol_ma = sma(volume, 24)
        price_ma = sma(close, 24)
        divergences = 0
        div_rets = []
        for i in range(48, len(close) - 24):
            if (not np.isnan(vol_ma[i]) and not np.isnan(vol_ma[i-24]) and
                not np.isnan(price_ma[i]) and not np.isnan(price_ma[i-24])):
                price_rising = price_ma[i] > price_ma[i-24]
                vol_falling = vol_ma[i] < vol_ma[i-24]
                if price_rising and vol_falling:
                    ret = (close[i+24] - close[i]) / close[i]
                    div_rets.append(ret)
                    divergences += 1
        if divergences > 10:
            dr_arr = np.array(div_rets)
            print(f"    Price-volume divergence (price up, vol down):")
            print(f"      occurrences: {divergences}")
            print(f"      avg 24h fwd return: {np.mean(dr_arr):+.3%}")
            print(f"      reversal rate (neg return): {np.sum(dr_arr < 0)/len(dr_arr)*100:.0f}%")

# ── 8. Summary: Risk-adjusted alpha potential ─────────────────────────────

print("\n" + "=" * 80)
print("8. RISK-ADJUSTED ALPHA POTENTIAL")
print("=" * 80)

# Score each market
scores = {}
for m in MARKETS:
    score = 0
    reasons = []

    # Sharpe
    s = stats[m]["sharpe"]
    if abs(s) > 0.5:
        reasons.append(f"sharpe={s:+.2f}")

    # Volatility (higher = more opportunity)
    v = stats[m]["ann_vol"]
    reasons.append(f"vol={v:.0%}")

    # RSI edge: check 24h oversold long
    close = arrays[m]["close"]
    rsi = compute_rsi(close, 14)
    oversold_rets = []
    for i in range(len(rsi) - 24):
        if not np.isnan(rsi[i]) and rsi[i] < 30:
            ret = (close[i + 24] - close[i]) / close[i]
            oversold_rets.append(ret)
    if oversold_rets:
        rsi_edge = np.mean(oversold_rets)
        rsi_wr = np.sum(np.array(oversold_rets) > 0) / len(oversold_rets)
        reasons.append(f"RSI<30 24h edge={rsi_edge:+.2%} (wr={rsi_wr:.0%}, n={len(oversold_rets)})")
        score += rsi_edge * 100  # weight

    # BB contrarian edge
    mid, upper, lower = bollinger_bands(close, 20, 2.0)
    bb_rets = []
    for i in range(len(close) - 24):
        if not np.isnan(lower[i]) and close[i] < lower[i]:
            ret = (close[i + 24] - close[i]) / close[i]
            bb_rets.append(ret)
        elif not np.isnan(upper[i]) and close[i] > upper[i]:
            ret = -(close[i + 24] - close[i]) / close[i]
            bb_rets.append(ret)
    if bb_rets:
        bb_edge = np.mean(bb_rets)
        reasons.append(f"BB contrarian 24h edge={bb_edge:+.2%}")
        score += bb_edge * 100

    # Funding rate capture potential
    best_funding_edge = 0
    for venue, records in funding[m].items():
        rates = np.array([r["rate"] for r in records], dtype=np.float64)
        if len(rates) > 100:
            ann = np.mean(np.abs(rates)) * 8760
            if ann > best_funding_edge:
                best_funding_edge = ann
    reasons.append(f"best funding capture={best_funding_edge:.1%}/yr")
    score += best_funding_edge * 10

    # Volatility score (more vol = more trading opportunity)
    score += v * 10

    scores[m] = {"score": score, "reasons": reasons}

# Rank
ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)

print("\n  Ranking by alpha potential score:")
for rank, (m, info) in enumerate(ranked, 1):
    print(f"\n  #{rank} {m} (score: {info['score']:.2f})")
    for r in info["reasons"]:
        print(f"      - {r}")

# ── 9. Strongest edges summary ───────────────────────────────────────────

print("\n" + "=" * 80)
print("9. STRONGEST EDGES FOUND")
print("=" * 80)

edges = []

# Collect all edges across markets
for m in MARKETS:
    close = arrays[m]["close"]
    rsi = compute_rsi(close, 14)

    # RSI oversold -> 24h
    rets = []
    for i in range(len(rsi) - 24):
        if not np.isnan(rsi[i]) and rsi[i] < 30:
            rets.append((close[i + 24] - close[i]) / close[i])
    if rets:
        wr = np.sum(np.array(rets) > 0) / len(rets)
        edges.append({
            "strategy": f"RSI oversold (<30) mean reversion",
            "market": m,
            "horizon": "24h",
            "signals": len(rets),
            "win_rate": wr,
            "avg_return": np.mean(rets),
            "med_return": np.median(rets),
        })

    # RSI overbought -> 24h short
    rets = []
    for i in range(len(rsi) - 24):
        if not np.isnan(rsi[i]) and rsi[i] > 70:
            rets.append(-(close[i + 24] - close[i]) / close[i])
    if rets:
        wr = np.sum(np.array(rets) > 0) / len(rets)
        edges.append({
            "strategy": f"RSI overbought (>70) mean reversion",
            "market": m,
            "horizon": "24h",
            "signals": len(rets),
            "win_rate": wr,
            "avg_return": np.mean(rets),
            "med_return": np.median(rets),
        })

    # BB lower band bounce
    mid, upper, lower = bollinger_bands(close, 20, 2.0)
    rets = []
    for i in range(len(close) - 24):
        if not np.isnan(lower[i]) and close[i] < lower[i]:
            rets.append((close[i + 24] - close[i]) / close[i])
    if rets:
        wr = np.sum(np.array(rets) > 0) / len(rets)
        edges.append({
            "strategy": f"BB lower band bounce (long)",
            "market": m,
            "horizon": "24h",
            "signals": len(rets),
            "win_rate": wr,
            "avg_return": np.mean(rets),
            "med_return": np.median(rets),
        })

    # BB upper band continuation (momentum)
    rets = []
    for i in range(len(close) - 24):
        if not np.isnan(upper[i]) and close[i] > upper[i]:
            rets.append((close[i + 24] - close[i]) / close[i])
    if rets:
        wr = np.sum(np.array(rets) > 0) / len(rets)
        edges.append({
            "strategy": f"BB upper breakout momentum (long)",
            "market": m,
            "horizon": "24h",
            "signals": len(rets),
            "win_rate": wr,
            "avg_return": np.mean(rets),
            "med_return": np.median(rets),
        })

    # Funding rate capture
    for venue, records in funding[m].items():
        rates = np.array([r["rate"] for r in records], dtype=np.float64)
        if len(rates) > 100:
            avg = np.mean(rates)
            ann = avg * 8760
            if abs(ann) > 0.05:  # >5% annualized
                direction = "short" if avg > 0 else "long"
                edges.append({
                    "strategy": f"Funding capture ({direction} to collect) via {venue}",
                    "market": m,
                    "horizon": "ongoing",
                    "signals": len(rates),
                    "win_rate": (np.sum(rates > 0) if avg > 0 else np.sum(rates < 0)) / len(rates),
                    "avg_return": abs(ann),
                    "med_return": abs(np.median(rates)) * 8760,
                })

# Sort by avg_return descending
edges.sort(key=lambda e: abs(e["avg_return"]), reverse=True)

print("\n  Top edges ranked by expected return:\n")
print(f"  {'#':>3}  {'Strategy':<50} {'Market':<10} {'Horizon':<8} "
      f"{'Signals':>8} {'WinRate':>8} {'AvgRet':>10} {'MedRet':>10}")
print(f"  {'─'*3}  {'─'*50} {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

for i, e in enumerate(edges[:20], 1):
    wr_str = f"{e['win_rate']:.0%}"
    ar_str = f"{e['avg_return']:+.2%}" if e['horizon'] != 'ongoing' else f"{e['avg_return']:.1%}/yr"
    mr_str = f"{e['med_return']:+.2%}" if e['horizon'] != 'ongoing' else f"{e['med_return']:.1%}/yr"
    print(f"  {i:>3}  {e['strategy']:<50} {e['market']:<10} {e['horizon']:<8} "
          f"{e['signals']:>8} {wr_str:>8} {ar_str:>10} {mr_str:>10}")

# ── 10. Final recommendation ─────────────────────────────────────────────

print("\n" + "=" * 80)
print("10. FINAL RECOMMENDATIONS")
print("=" * 80)

# Best risk-adjusted market
best_market = ranked[0][0]
print(f"\n  Best risk-adjusted alpha potential: {best_market}")
print(f"    Reasons: {', '.join(ranked[0][1]['reasons'][:3])}")

# Best strategies
if edges:
    print(f"\n  Top 3 strategy candidates:")
    for i, e in enumerate(edges[:3], 1):
        print(f"    {i}. {e['strategy']} on {e['market']}")
        if e['horizon'] == 'ongoing':
            print(f"       Expected: {e['avg_return']:.1%}/yr annualized, "
                  f"consistency: {e['win_rate']:.0%}")
        else:
            print(f"       Expected: {e['avg_return']:+.2%} per signal over {e['horizon']}, "
                  f"win rate: {e['win_rate']:.0%}, signals: {e['signals']}")

# Pair trading opportunity
print(f"\n  Pair/relative-value opportunities:")
for i, m1 in enumerate(MARKETS):
    for j, m2 in enumerate(MARKETS):
        if j > i:
            c = corr[i, j]
            if c > 0.7:
                vol_ratio = stats[m1]["ann_vol"] / stats[m2]["ann_vol"]
                ret_diff = stats[m1]["ann_return"] - stats[m2]["ann_return"]
                print(f"    {m1}/{m2}: corr={c:.3f}, vol_ratio={vol_ratio:.2f}, "
                      f"return_spread={ret_diff:+.1%}")
                print(f"      -> High correlation enables pairs trading when spread deviates")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
