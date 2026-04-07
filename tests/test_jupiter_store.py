import os
import tempfile

from flint.models import BorrowSnapshot
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)  # DuckDB creates its own file; pre-existing empty files cause errors
    return FlintStore(path), path


def test_upsert_and_query_borrow_rates():
    store, path = _make_store()
    try:
        snapshots = [
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
            BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "dune"),
        ]
        count = store.upsert_borrow_rates(snapshots)
        assert count == 3

        results = store.query_borrow_rates("SOL-PERP", 1000, 3000)
        assert len(results) == 3
        assert results[0].ts == 1000
        assert results[0].rate_hourly == 0.00008
        assert results[2].source == "dune"
    finally:
        store.close()
        os.unlink(path)


def test_query_borrow_cumulative():
    store, path = _make_store()
    try:
        snapshots = [
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
            BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"),
            BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "rpc"),
        ]
        store.upsert_borrow_rates(snapshots)

        rate = store.query_borrow_cumulative("SOL-PERP", 2000)
        assert rate == 1.002

        rate = store.query_borrow_cumulative("SOL-PERP", 2500)
        assert rate == 1.002

        rate = store.query_borrow_cumulative("SOL-PERP", 500)
        assert rate is None
    finally:
        store.close()
        os.unlink(path)


def test_upsert_borrow_rates_upsert_semantics():
    store, path = _make_store()
    try:
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"),
        ])
        store.upsert_borrow_rates([
            BorrowSnapshot("SOL-PERP", 1000, 0.00012, 0.80, 1.005, "dune"),
        ])
        results = store.query_borrow_rates("SOL-PERP", 1000, 1000)
        assert len(results) == 1
        assert results[0].rate_hourly == 0.00012
        assert results[0].source == "dune"
    finally:
        store.close()
        os.unlink(path)


def test_query_borrow_rates_empty():
    store, path = _make_store()
    try:
        results = store.query_borrow_rates("SOL-PERP", 1000, 2000)
        assert results == []
    finally:
        store.close()
        os.unlink(path)
