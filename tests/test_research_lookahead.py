"""Slice 6.3 — the look-ahead / leakage linter (§8.5, §11, D27).

Proven here on hand-authored strategy source and plain-list frames (D26 — no
market-like data): the static AST pass catches each leak tell; a clean strategy
passes; the truncation probe catches a full-window normalization the static pass
would miss on a subtle case; the result wording is "no leak detected", never
"leak-free"; and the D27 universe check is present and suppressible.
"""

from __future__ import annotations

from flint.research import (
    DEGENERATE_LABEL_HORIZON,
    FUTURE_INDEX,
    FUTURE_SHIFT,
    TRUNCATION_DIVERGENCE,
    UNBOUNDED_AGGREGATE,
    UNIVERSE_LOOKAHEAD,
    analyze,
    check_label_horizon,
    lint_source,
    truncation_probe,
)

# A strategy that leaks in five distinct ways.
LEAKY = '''
class Leaky:
    params = dict(label_horizon=0)          # degenerate horizon

    def features(self, market, history, ctx):
        fut = history["close"].shift(-1)     # future shift
        norm = history["close"] / history["close"].max()   # unbounded aggregate
        nxt = history.iloc[i + 1]            # forward index
        return {"fut": fut, "norm": norm, "nxt": nxt}

    def pick_universe(self, frame):
        return frame.nlargest(20, "volume")  # survivorship ranking (D27)
'''

# A causally-clean strategy: rolling stats, backward shift, forward-looking horizon.
CLEAN = '''
class Clean:
    params = dict(label_horizon=24)

    def features(self, market, history, ctx):
        roll = history["close"].rolling(20).mean()   # bounded window — ok
        prev = history["close"].shift(1)             # past shift — ok
        expd = history["vol"].expanding().std()      # expanding — ok
        return {"roll": roll, "prev": prev, "expd": expd}

    def on_candle(self, candle, history, ctx):
        return None
'''


def _categories(findings):
    return {f.category for f in findings}


# --- 1. static AST pass catches each tell -------------------------------------


def test_static_pass_catches_every_leak_in_the_leaky_strategy():
    cats = _categories(lint_source(LEAKY))
    assert cats == {
        DEGENERATE_LABEL_HORIZON,
        FUTURE_SHIFT,
        UNBOUNDED_AGGREGATE,
        FUTURE_INDEX,
        UNIVERSE_LOOKAHEAD,
    }


def test_findings_carry_line_and_snippet():
    shift = next(f for f in lint_source(LEAKY) if f.category == FUTURE_SHIFT)
    assert shift.line is not None
    assert "shift(-1)" in shift.snippet


def test_clean_strategy_is_not_flagged():
    assert lint_source(CLEAN) == []


def test_rolling_and_expanding_aggregates_are_not_leaks():
    # the same aggregate is a leak unbounded, fine when windowed.
    assert _categories(lint_source("y = df['x'].mean()")) == {UNBOUNDED_AGGREGATE}
    assert lint_source("y = df['x'].rolling(5).mean()") == []
    assert lint_source("y = df['x'].expanding().std()") == []


def test_fit_inside_features_is_flagged_as_unbounded():
    src = "def features(self):\n    self.model.fit(X, y)\n"
    findings = lint_source(src)
    assert _categories(findings) == {UNBOUNDED_AGGREGATE}
    assert "features()" in findings[0].message


def test_backward_shift_and_last_row_iloc_are_not_flagged():
    # shift(1) is the past; iloc[-1] is the current/last row, not the future.
    assert lint_source("a = df['x'].shift(1)") == []
    assert lint_source("a = df.iloc[-1]") == []


# --- 2. degenerate label horizon (literal + runtime) --------------------------


def test_degenerate_label_horizon_literal_and_runtime():
    assert _categories(lint_source("params = dict(label_horizon=0)")) == {
        DEGENERATE_LABEL_HORIZON
    }
    assert lint_source("params = dict(label_horizon=24)") == []
    assert check_label_horizon(0) is not None
    assert check_label_horizon(-3) is not None
    assert check_label_horizon(1) is None


# --- 3. D27 universe check present and suppressible ---------------------------


def test_universe_check_flags_rankers_and_can_be_disabled():
    src = "top = frame.nlargest(20, 'volume')"
    assert _categories(lint_source(src)) == {UNIVERSE_LOOKAHEAD}
    assert lint_source(src, check_universe=False) == []


# --- 4. truncation probe (the dynamic check) ----------------------------------


def _normalize_by_full_max(frame):
    m = max(frame)
    return [x / m for x in frame]


def _backward_diff(frame):
    return [frame[i] - (frame[i - 1] if i > 0 else 0) for i in range(len(frame))]


def test_truncation_probe_catches_full_window_normalization():
    # dividing by the whole-frame max shifts earlier rows when the max is truncated.
    findings = truncation_probe(_normalize_by_full_max, [1.0, 2.0, 3.0, 10.0])
    assert findings and all(f.category == TRUNCATION_DIVERGENCE for f in findings)


def test_truncation_probe_passes_a_backward_only_feature():
    assert truncation_probe(_backward_diff, [1.0, 2.0, 3.0, 10.0]) == []


def test_truncation_probe_names_only_the_changed_column():
    def mixed(frame):
        m = max(frame)
        return [{"norm": x / m, "raw": x} for x in frame]

    findings = truncation_probe(mixed, [1.0, 2.0, 3.0, 10.0])
    assert findings
    joined = " ".join(f.message for f in findings)
    assert "norm" in joined and "raw" not in joined


# --- 5. composite analyze + honest wording ------------------------------------


def test_analyze_clean_reports_no_leak_detected_never_leak_free():
    result = analyze(
        CLEAN,
        label_horizon=24,
        feature_fn=_backward_diff,
        frame=[1.0, 2.0, 3.0, 10.0],
    )
    assert not result.leak_detected
    text = result.summary()
    assert "no leak detected" in text
    assert "leak-free" not in text  # presence, never absence
    # every applicable check ran and is recorded — no overstated coverage.
    assert set(result.checks_run) == {
        "ast_static",
        "universe_d27",
        "label_horizon",
        "truncation_probe",
    }
    assert result.blind_spots  # documented limits always attached


def test_analyze_leaky_detects_and_lists_categories():
    result = analyze(
        LEAKY,
        label_horizon=0,
        feature_fn=_normalize_by_full_max,
        frame=[1.0, 2.0, 3.0, 10.0],
    )
    assert result.leak_detected
    assert TRUNCATION_DIVERGENCE in result.categories()
    assert DEGENERATE_LABEL_HORIZON in result.categories()
    assert "leak-free" not in result.summary()
