"""Arrow IPC wire codec for the Flint Data API (§9.0).

The Data API's range endpoint serves an **Arrow IPC stream** — the v1 serving
contract (§9.0). Both sides of the wire (the FastAPI service and the local
client) encode/decode through here so the byte format has one definition, and the
media type is named once. A DuckDB/Parquet backend reads the same Arrow tables
natively, so nothing is transcoded on the way in or out.
"""

from __future__ import annotations

import pyarrow as pa

# The IANA media type for an Arrow IPC stream — what the range endpoint returns.
ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


def to_ipc(table: pa.Table) -> bytes:
    """Serialize ``table`` to Arrow IPC stream bytes (schema preserved)."""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def from_ipc(raw: bytes) -> pa.Table:
    """Decode Arrow IPC stream ``bytes`` back into a Table."""
    with pa.ipc.open_stream(pa.BufferReader(raw)) as reader:
        return reader.read_all()
