"""Slice 5.3 — the declarative ML surface, managed model store, feature-causality (§8.5, D19).

What is proven here, all with hand-authored candle inputs (D26 — no synthetic
market data):

1. **Declarative base** — features/target/train/decide raise until overridden;
   ``params`` are per-instance (optimizer-safe).
2. **Causal training-set build + right-edge purge** — each row sees only history
   up to its anchor; unfinished labels are dropped.
3. **FutureWindow** — return/max/min are computed from the forward window only.
4. **MLContext** — adds ``model_store``/``seed``, passes everything else through.
5. **MLEngineStrategy lifecycle** — retrains on the ``retrain_days`` schedule (not
   before warmup, not every bar), caches the model, no-ops until one exists, and
   still enforces the D28 venue gate.
6. **Managed model store** — scoped (tenant, strategy) with no cross-leak on a
   shared backend; save/load/exists/keys/delete; quota; raw pickle refused.
7. **Feature-causality screen** — unbounded aggregations in ``features()`` flagged,
   bounded ones clean, and the rule is scoped to ``features()``.
8. **Real codec round-trips** — lightgbm/xgboost/sklearn, skipped when absent.
"""

from __future__ import annotations

import json

import pytest

from flint.core.models import Candle, Signal
from flint.strategy import (
    FutureWindow,
    MLContext,
    MLEngineStrategy,
    MLStrategy,
    ModelNotFound,
    ModelStore,
    ModelStoreQuota,
    ModelStoreQuotaError,
    UnsafeModelError,
    build_training_set,
)
from flint.strategy.model_store import ModelCodec, SerializedModel
from flint.strategy.sandbox import screen_or_raise, screen_source
from flint.strategy.sandbox.screen import StrategyScreenError
from flint.ports import TenantContext

HL = "hyperliquid"
MKT = "SOL-PERP"


# --- hand-authored fixtures ---------------------------------------------------


def _c(ts: int, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    """One hand-authored bar; OHLC collapse to close unless a high/low is given."""
    return Candle(
        ts=ts,
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=0.0,
        market=MKT,
        resolution_s=3600,
        venue=HL,
    )


def _path(closes: list[float]) -> list[Candle]:
    return [_c(i * 3_600_000, c) for i, c in enumerate(closes)]


class _FakeModel:
    """A hand-authored 'model' — a payload we can serialise to plain JSON."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload


def _fake_codec() -> ModelCodec:
    return ModelCodec(
        name="fake-json",
        matches=lambda o: isinstance(o, _FakeModel),
        serialize=lambda o: json.dumps(o.payload).encode("utf-8"),
        deserialize=lambda b: _FakeModel(json.loads(b.decode("utf-8"))),
    )


def _store(tenant: str = "local", strategy_id: str = "s", **kw) -> ModelStore:
    return ModelStore(
        TenantContext(tenant_id=tenant),
        strategy_id,
        codecs=[_fake_codec()],
        **kw,
    )


class _FakeCtx:
    """A minimal engine-ctx double: closed candles + now + submit_order."""

    def __init__(self, closed: list[Candle], now: int) -> None:
        self._closed = closed
        self.now = now
        self.rng = object()
        self.submitted: list = []

    def candles(self, market: str, lookback: int, venue: str | None = None) -> list[Candle]:
        return list(self._closed) if lookback > 0 else []

    def submit_order(self, order) -> str:  # noqa: ANN001 - test double
        self.submitted.append(order)
        return "oid"


# --- 1. declarative base ------------------------------------------------------


def test_ml_hooks_raise_until_overridden():
    s = MLStrategy()
    with pytest.raises(NotImplementedError):
        s.features(MKT, _path([1, 2]), None)
    with pytest.raises(NotImplementedError):
        s.target(MKT, _path([1, 2]), FutureWindow(candles=(), anchor_close=1.0))
    with pytest.raises(NotImplementedError):
        s.train([], [], None)


def test_ml_params_are_per_instance_and_overridable():
    a = MLStrategy(retrain_days=1)
    b = MLStrategy()
    assert a.params["retrain_days"] == 1
    assert b.params["retrain_days"] == 7  # untouched class default
    assert a.params is not MLStrategy.params
    assert a.model_key == "clf"
    # param_spec exposes the ML knobs to the optimizer/UI
    assert "label_horizon" in a.param_spec()


# --- 2. causal training-set build + right-edge purge --------------------------


class _RecordingML(MLStrategy):
    """Records the history length it saw per feature call, to prove causality."""

    params = dict(retrain_days=1, label_horizon=2, warmup_bars=1, threshold=0.6, model_key="clf")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.seen_lengths: list[int] = []

    def features(self, market, history, ctx):
        self.seen_lengths.append(len(history))
        return {"close": history[-1].close}

    def target(self, market, history, future_window):
        return 1 if future_window.return_pct > 0 else 0


def test_build_training_set_is_causal_and_right_edge_purged():
    closed = _path([10, 11, 12, 11, 13, 14])  # n=6
    s = _RecordingML()
    X, y = build_training_set(s, MKT, closed, None, label_horizon=2, warmup=1)
    # warmup=1 drops anchor 0; label_horizon=2 needs i+3 <= 6 -> anchors 1..3.
    assert len(X) == len(y) == 3
    # Each feature call saw history truncated exactly at its anchor (causal).
    assert s.seen_lengths == [2, 3, 4]
    # Labels: anchor close vs forward window's last close.
    # i=1 close=11 -> fwd [12,11] last 11 -> 0 ; i=2 close=12 -> fwd [11,13] last 13 -> 1
    # i=3 close=11 -> fwd [13,14] last 14 -> 1
    assert y == [0, 1, 1]


def test_build_training_set_empty_when_history_too_short():
    closed = _path([10, 11])  # not enough for a single closed label at horizon 2
    X, y = build_training_set(_RecordingML(), MKT, closed, None, label_horizon=2, warmup=1)
    assert X == [] and y == []


# --- 3. FutureWindow ----------------------------------------------------------


def test_future_window_return_max_min():
    window = FutureWindow(
        candles=(_c(0, 105, high=110, low=104), _c(1, 108, high=112, low=100)),
        anchor_close=100.0,
    )
    assert window.return_pct == pytest.approx(8.0)  # last close 108 vs 100
    assert window.max_return_pct == pytest.approx(12.0)  # high 112
    assert window.min_return_pct == pytest.approx(0.0)  # low 100


def test_future_window_empty_is_zero():
    empty = FutureWindow(candles=(), anchor_close=100.0)
    assert empty.return_pct == 0.0 and empty.max_return_pct == 0.0


# --- 4. MLContext -------------------------------------------------------------


def test_ml_context_adds_store_and_seed_and_passes_through():
    ctx = _FakeCtx(closed=[], now=42)
    store = _store()
    ml_ctx = MLContext(ctx, store, seed=7)
    assert ml_ctx.model_store is store
    assert ml_ctx.seed == 7
    assert ml_ctx.now == 42  # passed straight through
    assert ml_ctx.rng is ctx.rng


# --- 5. MLEngineStrategy lifecycle -------------------------------------------


class _TrainOnceML(MLStrategy):
    params = dict(retrain_days=1, label_horizon=1, warmup_bars=2, threshold=0.6, model_key="clf")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.train_calls = 0
        self.seeds_seen: list[int] = []

    def features(self, market, history, ctx):
        return {"close": history[-1].close}

    def target(self, market, history, future_window):
        return 1 if future_window.return_pct > 0 else 0

    def train(self, X, y, ctx):
        self.train_calls += 1
        self.seeds_seen.append(ctx.seed)
        ctx.model_store.save(self.model_key, _FakeModel({"n": len(X)}))

    def decide(self, candle, features, model, ctx):
        return [Signal.long(candle.market, HL, size_usd=100.0)]


def _run_bar(adapter, closed, now):
    return adapter.on_candle(_c(now, closed[-1].close if closed else 100.0), _FakeCtx(closed, now))


def test_ml_engine_noop_until_model_then_trains_and_trades():
    strat = _TrainOnceML()
    adapter = MLEngineStrategy(strat, _store(), seed=11)
    closed = _path([10, 11, 12, 13])  # 4 closed bars, warmup 2 + horizon 1 + 1 = 4 -> eligible

    # Too little history -> no train, no model, no-op.
    out = adapter.on_candle(_c(0, 10.0), _FakeCtx(_path([10]), now=0))
    assert out == [] and strat.train_calls == 0

    # Enough closed history + due -> trains once, model cached, decide fires.
    out = adapter.on_candle(_c(4 * 3_600_000, 14.0), _FakeCtx(closed, now=4 * 3_600_000))
    assert strat.train_calls == 1
    assert adapter.retrain_count == 1
    assert adapter.model_store.exists("clf")
    assert len(out) == 1 and out[0].action == "long"
    assert strat.seeds_seen == [11]  # fixed seed threaded to train (determinism)


def test_ml_engine_respects_retrain_schedule():
    strat = _TrainOnceML(retrain_days=2)
    adapter = MLEngineStrategy(strat, _store(), seed=0)
    closed = _path([10, 11, 12, 13, 14, 15])
    day = 86_400_000

    adapter.on_candle(_c(0, 15.0), _FakeCtx(closed, now=0))
    assert strat.train_calls == 1
    # 1 day later: not due (retrain_days=2) -> no retrain.
    adapter.on_candle(_c(day, 15.0), _FakeCtx(closed, now=day))
    assert strat.train_calls == 1
    # 2 days later: due -> retrain.
    adapter.on_candle(_c(2 * day, 15.0), _FakeCtx(closed, now=2 * day))
    assert strat.train_calls == 2


def test_ml_engine_enforces_venue_gate():
    class _BadVenueML(_TrainOnceML):
        def decide(self, candle, features, model, ctx):
            return [Signal.long(candle.market, "binance", size_usd=100.0)]

    strat = _BadVenueML()
    adapter = MLEngineStrategy(strat, _store(), seed=0)
    closed = _path([10, 11, 12, 13])
    out = adapter.on_candle(_c(4 * 3_600_000, 14.0), _FakeCtx(closed, now=4 * 3_600_000))
    assert out == []  # binance leg gated out
    rej = adapter.drain_rejections()
    assert len(rej) == 1 and rej[0].reason == "venue_not_executable"


def test_ml_engine_tearsheet_note_carries_retrain_count():
    strat = _TrainOnceML()
    adapter = MLEngineStrategy(strat, _store(), seed=0)
    adapter.on_candle(_c(4 * 3_600_000, 14.0), _FakeCtx(_path([10, 11, 12, 13]), now=4 * 3_600_000))
    notes = adapter.tearsheet_notes()
    assert any("retrain" in n for n in notes)


# --- 6. managed model store ---------------------------------------------------


def test_model_store_save_load_exists_keys_delete():
    s = _store()
    assert s.keys() == [] and not s.exists("clf")
    s.save("clf", _FakeModel({"w": 1}))
    assert s.exists("clf") and s.keys() == ["clf"]
    assert s.load("clf").payload == {"w": 1}
    s.save("reg", _FakeModel({"w": 2}))
    assert s.keys() == ["clf", "reg"]  # sorted
    s.delete("clf")
    assert not s.exists("clf") and s.keys() == ["reg"]


def test_model_store_missing_load_and_delete_raise():
    s = _store()
    with pytest.raises(ModelNotFound):
        s.load("nope")
    with pytest.raises(ModelNotFound):
        s.delete("nope")


def test_model_store_scoping_no_cross_tenant_or_strategy_leak():
    backend: dict = {}
    a = _store(tenant="alice", strategy_id="s1", backend=backend)
    b = _store(tenant="bob", strategy_id="s1", backend=backend)
    c = _store(tenant="alice", strategy_id="s2", backend=backend)
    a.save("clf", _FakeModel({"who": "alice"}))
    # Same key, different tenant/strategy: never visible.
    assert not b.exists("clf") and b.keys() == []
    assert not c.exists("clf") and c.keys() == []
    with pytest.raises(ModelNotFound):
        b.load("clf")
    assert a.load("clf").payload == {"who": "alice"}


def test_model_store_refuses_unknown_object_never_pickles():
    s = _store()
    # A plain object matches no safe codec -> refused, not silently pickled.
    with pytest.raises(UnsafeModelError):
        s.save("m", object())
    with pytest.raises(UnsafeModelError):
        s.save("m", {"raw": "dict"})


def test_model_store_key_quota_enforced():
    s = _store(quota=ModelStoreQuota(max_keys=1, max_bytes=10**9))
    s.save("a", _FakeModel({"x": 1}))
    with pytest.raises(ModelStoreQuotaError):
        s.save("b", _FakeModel({"x": 2}))
    # Overwriting the existing key is fine (no new key).
    s.save("a", _FakeModel({"x": 3}))
    assert s.load("a").payload == {"x": 3}


def test_model_store_byte_quota_enforced():
    s = _store(quota=ModelStoreQuota(max_keys=10, max_bytes=20))
    with pytest.raises(ModelStoreQuotaError):
        s.save("big", _FakeModel({"padding": "x" * 100}))


def test_model_store_rejects_empty_key():
    from flint.strategy.model_store import ModelStoreError

    with pytest.raises(ModelStoreError):
        _store().save("", _FakeModel({"x": 1}))


# --- 7. feature-causality screen ----------------------------------------------


def _feat(body: str) -> str:
    return (
        "class S:\n"
        "    def features(self, market, history, ctx):\n"
        f"        {body}\n"
    )


def _codes(src: str) -> list[str]:
    return [v.code for v in screen_source(src)]


@pytest.mark.parametrize(
    "expr",
    [
        "history.mean()",
        "history.std()",
        "df['close'].sum()",
        "prices.max()",
        "model.fit(x, y)",
    ],
)
def test_unbounded_aggregation_in_features_is_flagged(expr):
    assert "feature-lookahead" in _codes(_feat(f"return {expr}"))


@pytest.mark.parametrize(
    "expr",
    [
        "history[-20:].mean()",
        "df.rolling(20).mean()",
        "df['close'].rolling(20).std()",
        "series.expanding().mean()",
        "series.ewm(span=10).mean()",
        "df['close'].tail(50).sum()",
    ],
)
def test_bounded_aggregation_in_features_is_clean(expr):
    assert _codes(_feat(f"return {expr}")) == []


def test_free_function_aggregation_bounded_vs_unbounded():
    assert "feature-lookahead" in _codes(_feat("return np.mean(history)"))
    assert _codes(_feat("return np.mean(history[-20:])")) == []
    assert "feature-lookahead" in _codes(_feat("return np.polyfit(history, y, 1)"))


def test_causality_rule_scoped_to_features_only():
    # The same full-window aggregate in on_candle is legitimate — not flagged.
    src = (
        "class S:\n"
        "    def on_candle(self, candle, history, ctx):\n"
        "        return history.mean()\n"
    )
    assert "feature-lookahead" not in _codes(src)


def test_screen_or_raise_reports_feature_lookahead():
    with pytest.raises(StrategyScreenError) as exc:
        screen_or_raise(_feat("return history.mean()"))
    assert any(v.code == "feature-lookahead" for v in exc.value.violations)


# --- 8. real codec round-trips (skipped when the library is absent) -----------


def test_lightgbm_round_trip_through_store():
    lgb = pytest.importorskip("lightgbm")
    import numpy as np

    # Hand-authored separable rows (unit inputs, not market data).
    X = np.array([[0.0], [0.1], [0.2], [1.0], [1.1], [1.2]])
    y = np.array([0, 0, 0, 1, 1, 1])
    model = lgb.LGBMClassifier(n_estimators=5, min_child_samples=1, random_state=0).fit(X, y)

    store = ModelStore(TenantContext.local(), "s")  # default (real) codecs
    store.save("clf", model)
    booster = store.load("clf")
    assert booster.model_to_string()  # a native lightgbm Booster came back
    assert store.exists("clf")


def test_xgboost_round_trip_through_store():
    xgb = pytest.importorskip("xgboost")
    import numpy as np

    X = np.array([[0.0], [0.1], [1.0], [1.1]])
    y = np.array([0, 0, 1, 1])
    model = xgb.XGBClassifier(n_estimators=3, max_depth=2, random_state=0).fit(X, y)

    store = ModelStore(TenantContext.local(), "s")
    store.save("clf", model)
    booster = store.load("clf")
    import xgboost as _xgb

    assert isinstance(booster, _xgb.Booster)


def test_sklearn_via_onnx_round_trip_through_store():
    pytest.importorskip("sklearn")
    pytest.importorskip("skl2onnx")
    pytest.importorskip("onnxruntime")
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    X = np.array([[0.0], [0.1], [1.0], [1.1]], dtype="float32")
    y = np.array([0, 0, 1, 1])
    model = LogisticRegression().fit(X, y)

    store = ModelStore(TenantContext.local(), "s")
    store.save("clf", model)
    session = store.load("clf")  # an onnxruntime InferenceSession
    assert session.get_inputs()  # a usable ONNX session, no pickle involved


def test_serialized_model_is_inert_bytes_no_pickle():
    # The at-rest artifact is a format name + inert bytes — never a pickle stream.
    sm = SerializedModel(fmt="fake-json", blob=b'{"w": 1}')
    assert isinstance(sm.blob, bytes) and sm.fmt == "fake-json"
