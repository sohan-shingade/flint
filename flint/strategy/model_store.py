"""The managed model store — the ONLY way an ML strategy persists a model (§8.5, D19).

A QuantConnect-ObjectStore-style key-value store — ``save``/``load``/``exists``/
``keys``/``delete`` — scoped server-side to ``(tenant_id, strategy_id)``, quota-
limited, and **no filesystem path is ever exposed** to strategy code. The strategy
hands over a fitted model object; the *platform* serialises it in a **safe format**
(xgboost/lightgbm native JSON boosters; sklearn via ONNX) and refuses anything it
does not recognise. Raw ``pickle``/``joblib``/``torch.load`` bytes are **never**
written or read — that is the #1 ML-supply-chain RCE vector (§8.5, the sandbox
unsafe-deserialization ban), so an unknown object is *refused*, never pickled.

The store is scoped by construction: two stores over the *same* backend but
different ``(tenant_id, strategy_id)`` never see each other's keys — the same
cross-leak contract the ``UserDataPort`` obeys (§2.7). The v1 backend is an
in-memory dict (a ``UserDataPort``-backed backend is a v1.x concern); the safety
and scoping guarantees live here, above whatever the backend is.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from flint.ports import TenantContext

# Backend key: the full scope tuple. A store only ever reads/writes rows whose
# (tenant_id, strategy_id) match its own, so a shared backend cannot leak across
# tenants or strategies.
_ScopeKey = tuple[str, str, str]


class ModelStoreError(Exception):
    """Base for every managed-model-store failure (§8.5)."""


class UnsafeModelError(ModelStoreError):
    """Refused: the object has no safe serialisation and would need raw pickle (§8.5).

    The store never falls back to ``pickle``/``joblib``/``torch.load`` — an object
    that no safe codec recognises is rejected outright, because loading a model
    from arbitrary bytes is the ML-supply-chain RCE vector §8.5 exists to close.
    """


class ModelStoreQuotaError(ModelStoreError):
    """Refused: the write would exceed the store's key-count or byte quota (§8.5)."""


class ModelNotFound(ModelStoreError, KeyError):
    """No model under this key **for this tenant/strategy** (a scoped miss, §8.5)."""


@dataclass(frozen=True, slots=True)
class SerializedModel:
    """A model at rest: the safe-format name plus the inert bytes it serialised to.

    ``fmt`` names the codec that produced ``blob`` (e.g. ``"lightgbm-json"``); load
    dispatches on it. There is no pickle here — ``blob`` is native-JSON or ONNX.
    """

    fmt: str
    blob: bytes


@dataclass(frozen=True, slots=True)
class ModelCodec:
    """One safe (de)serialiser: ``matches`` decides, ``serialize``/``deserialize`` act.

    ``matches`` must classify by *type/module name only* — never by importing the
    heavy library — so a laptop without xgboost installed still constructs the
    registry. The library import is lazy, inside ``serialize``/``deserialize``.
    """

    name: str
    matches: Callable[[Any], bool]
    serialize: Callable[[Any], bytes]
    deserialize: Callable[[bytes], Any]


@dataclass(frozen=True, slots=True)
class ModelStoreQuota:
    """The ceiling a store enforces on every ``save`` (§8.5 quota-limited)."""

    max_keys: int = 32
    max_bytes: int = 256 * 1024 * 1024  # 256 MiB per (tenant, strategy)

    @classmethod
    def default(cls) -> "ModelStoreQuota":
        return cls()


def _module_root(obj: Any) -> str:
    return type(obj).__module__.split(".", 1)[0]


def _xgboost_codec() -> ModelCodec:
    """xgboost Booster / sklearn-wrapper → native JSON booster (no pickle, §8.5)."""

    def _ser(obj: Any) -> bytes:
        booster = obj.get_booster() if hasattr(obj, "get_booster") else obj
        return bytes(booster.save_raw(raw_format="json"))

    def _de(blob: bytes) -> Any:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(bytearray(blob))
        return booster

    return ModelCodec("xgboost-json", lambda o: _module_root(o) == "xgboost", _ser, _de)


def _lightgbm_codec() -> ModelCodec:
    """lightgbm Booster / sklearn-wrapper → native text model string (no pickle)."""

    def _ser(obj: Any) -> bytes:
        booster = obj.booster_ if hasattr(obj, "booster_") else obj
        return booster.model_to_string().encode("utf-8")

    def _de(blob: bytes) -> Any:
        import lightgbm as lgb

        return lgb.Booster(model_str=blob.decode("utf-8"))

    return ModelCodec("lightgbm-json", lambda o: _module_root(o) == "lightgbm", _ser, _de)


def _sklearn_codec() -> ModelCodec:
    """sklearn estimator → ONNX (raw estimators refused; §8.5 "sklearn via ONNX").

    Deserialisation returns an ``onnxruntime.InferenceSession`` — the safe-format
    tradeoff §8.5 names: a sklearn model comes back as an ONNX session (predict via
    ``sess.run``), never a re-hydrated pickled estimator.
    """

    def _ser(obj: Any) -> bytes:
        try:
            from skl2onnx import convert_sklearn
            from skl2onnx.common.data_types import FloatTensorType
        except ImportError as exc:  # pragma: no cover - exercised only w/o skl2onnx
            raise UnsafeModelError(
                "sklearn models persist only via ONNX (skl2onnx not installed); "
                "raw pickle is refused (§8.5)"
            ) from exc
        n_features = int(getattr(obj, "n_features_in_", 0)) or 1
        onx = convert_sklearn(
            obj, initial_types=[("input", FloatTensorType([None, n_features]))]
        )
        return onx.SerializeToString()

    def _de(blob: bytes) -> Any:
        import onnxruntime as rt

        return rt.InferenceSession(blob)

    return ModelCodec("sklearn-onnx", lambda o: "sklearn" in type(o).__module__, _ser, _de)


def default_codecs() -> list[ModelCodec]:
    """The built-in safe codecs, in match order (specific vendors before sklearn).

    xgboost/lightgbm sklearn-wrappers live under ``*.sklearn`` (so their module
    string contains ``"sklearn"``); the vendor codecs are ordered first so those
    wrappers serialise as native boosters rather than through ONNX.
    """
    return [_xgboost_codec(), _lightgbm_codec(), _sklearn_codec()]


class ModelStore:
    """A tenant/strategy-scoped, quota'd, safe-format model KV (``ctx.model_store``).

    Every key is namespaced ``(tenant_id, strategy_id, key)`` in the backend, so a
    store never observes another scope's models even when the backend is shared —
    ``keys()``/``exists()``/``load()``/``delete()`` all filter to this scope. Writes
    go through the codec registry: a recognised model is serialised to a safe format
    and quota-checked; an unrecognised object is refused (never pickled).
    """

    def __init__(
        self,
        tenant: TenantContext,
        strategy_id: str,
        *,
        backend: MutableMapping[_ScopeKey, SerializedModel] | None = None,
        quota: ModelStoreQuota | None = None,
        codecs: list[ModelCodec] | None = None,
    ) -> None:
        self._tenant_id = tenant.tenant_id
        self._strategy_id = strategy_id
        self._backend: MutableMapping[_ScopeKey, SerializedModel] = (
            backend if backend is not None else {}
        )
        self._quota = quota or ModelStoreQuota.default()
        self._codecs = list(codecs) if codecs is not None else default_codecs()

    def _scope(self, key: str) -> _ScopeKey:
        return (self._tenant_id, self._strategy_id, key)

    def _own_items(self) -> list[tuple[str, SerializedModel]]:
        return [
            (k[2], v)
            for k, v in self._backend.items()
            if k[0] == self._tenant_id and k[1] == self._strategy_id
        ]

    def _pick_codec(self, obj: Any) -> ModelCodec:
        for codec in self._codecs:
            if codec.matches(obj):
                return codec
        raise UnsafeModelError(
            f"no safe serialiser for {type(obj).__module__}.{type(obj).__name__}; "
            "models persist only in safe formats (xgboost/lightgbm JSON, sklearn "
            "via ONNX) — raw pickle is refused (§8.5)"
        )

    def _codec_by_name(self, name: str) -> ModelCodec:
        for codec in self._codecs:
            if codec.name == name:
                return codec
        raise ModelStoreError(f"no codec registered for format {name!r}")

    def save(self, key: str, obj: Any) -> None:
        """Serialise ``obj`` to a safe format and persist it under ``key`` (§8.5).

        Raises :class:`UnsafeModelError` if no safe codec matches (never pickle),
        and :class:`ModelStoreQuotaError` if the write would breach the key-count
        or byte quota. Overwriting an existing key replaces its bytes.
        """
        if not key:
            raise ModelStoreError("model key must be a non-empty string")
        codec = self._pick_codec(obj)
        blob = codec.serialize(obj)
        record = SerializedModel(fmt=codec.name, blob=blob)

        items = dict(self._own_items())
        prior = items.get(key)
        n_keys = len(items) + (0 if prior is not None else 1)
        n_bytes = (
            sum(len(v.blob) for v in items.values())
            - (len(prior.blob) if prior is not None else 0)
            + len(blob)
        )
        if n_keys > self._quota.max_keys:
            raise ModelStoreQuotaError(
                f"model-store key quota exceeded ({n_keys} > {self._quota.max_keys})"
            )
        if n_bytes > self._quota.max_bytes:
            raise ModelStoreQuotaError(
                f"model-store byte quota exceeded ({n_bytes} > {self._quota.max_bytes})"
            )
        self._backend[self._scope(key)] = record

    def load(self, key: str) -> Any:
        """Deserialise and return the model under ``key`` (this scope only).

        Raises :class:`ModelNotFound` if absent for this tenant/strategy — a key
        owned by another scope is indistinguishable from a genuine miss.
        """
        record = self._backend.get(self._scope(key))
        if record is None:
            raise ModelNotFound(key)
        return self._codec_by_name(record.fmt).deserialize(record.blob)

    def exists(self, key: str) -> bool:
        """True iff a model is stored under ``key`` for this tenant/strategy."""
        return self._scope(key) in self._backend

    def keys(self) -> list[str]:
        """Every model key in this scope, sorted (never another scope's)."""
        return sorted(k for k, _ in self._own_items())

    def delete(self, key: str) -> None:
        """Remove ``key`` from this scope; raise :class:`ModelNotFound` if absent."""
        scoped = self._scope(key)
        if scoped not in self._backend:
            raise ModelNotFound(key)
        del self._backend[scoped]
