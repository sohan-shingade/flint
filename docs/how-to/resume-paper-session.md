# How to: Resume a paper trading session

Paper sessions persist in DuckDB and auto-resume on server restart. For manual control:

## Automatic resume

`flint serve` calls `engine.resume_sessions()` at startup. Sessions with `status="running"` are loaded, positions restored, and replay-forward fills any candles missed while offline.

## Redeploy from a new start

Rewind a session to replay from a specific time:

```bash
curl -X POST localhost:8000/api/v1/paper/redeploy \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<id>","replay_start_ts":1730000000}'
```

Returns a new `session_id`. The old session is archived.

## Redeploy all active sessions

```bash
curl -X POST localhost:8000/api/v1/paper/redeploy-all \
  -d '{"replay_start_ts":1730000000}'
```

## Check stale live sessions

On server restart, `live_sessions` with `status="running"` are marked `interrupted` — they can't auto-resume (need venue credentials). Redeploy manually via the UI's Live Trading page.

## Stop vs kill

| Endpoint | Behavior |
|---|---|
| `POST /paper/stop` | Close all positions with reduce-only orders, save final state |
| `POST /paper/kill` | Force-stop without closing positions |

Use **stop** by default. **Kill** only if a session is stuck or closing is rejected by risk guards.

## Update risk mid-flight

```bash
curl -X POST localhost:8000/api/v1/paper/$SESSION_ID/risk \
  -d '{"max_drawdown_pct": 0.08}'
```

Takes effect on the next bar.

## Related

- [reference/rest-api.md#paper-trading](../reference/rest-api.md#paper-trading) — full paper endpoint list
- [tutorials/04-paper-to-live.md](../tutorials/04-paper-to-live.md)
