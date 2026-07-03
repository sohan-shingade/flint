"""The parent<->child wire codec (§8.3).

Data crosses the process boundary as **serialized Arrow/dicts over a pipe** — the
child holds no reference to parent memory, only copied bytes. Two value kinds are
supported: JSON for plain dicts/lists/scalars, and Arrow IPC (base64-wrapped) for
tabular ``pyarrow.Table``.

Security note: the child is untrusted, so the parent decodes its output with
**JSON and Arrow only — never pickle/marshal**. JSON parsing executes no code,
and Arrow IPC carries data, not code. This is the #1 rule that keeps a malicious
strategy's *output* from becoming host RCE (§8.3 unsafe-deserialization ban).
"""

from __future__ import annotations

import base64
from typing import Any

import pyarrow as pa

_JSON_SCALARS = (bool, int, float, str, list, dict)


def encode_value(obj: Any) -> dict[str, Any]:
    """Encode a value to a ``{"kind", "data"}`` envelope. Raises on unsupported."""
    if obj is None or isinstance(obj, _JSON_SCALARS):
        return {"kind": "json", "data": obj}
    if isinstance(obj, pa.Table):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, obj.schema) as writer:
            writer.write_table(obj)
        data = base64.b64encode(sink.getvalue().to_pybytes()).decode("ascii")
        return {"kind": "arrow", "data": data}
    raise TypeError(f"cannot serialize {type(obj).__name__} across the sandbox")


def decode_value(env: dict[str, Any]) -> Any:
    """Decode a ``{"kind", "data"}`` envelope. Uses JSON/Arrow only, never pickle."""
    kind = env["kind"]
    if kind == "json":
        return env["data"]
    if kind == "arrow":
        buf = pa.py_buffer(base64.b64decode(env["data"]))
        with pa.ipc.open_stream(pa.BufferReader(buf)) as reader:
            return reader.read_all()
    raise ValueError(f"unknown encoded kind {kind!r}")
