# How to: Add API keys

Most providers (Drift, Hyperliquid, Pyth, Raydium, Orca, GeckoTerminal, Jupiter, CoinGecko, CCXT public) need **no keys**. These do:

| Provider | Env var | Free tier | Sign-up |
|---|---|---|---|
| Birdeye | `FLINT_BIRDEYE_API_KEY` | yes | [birdeye.so/developers](https://birdeye.so/developers) |
| Helius | `FLINT_HELIUS_API_KEY` | yes (no CC) | [helius.dev](https://helius.dev) |
| Dune | `FLINT_DUNE_API_KEY` | limited | [dune.com](https://dune.com) |
| Tardis | `FLINT_TARDIS_API_KEY` | paid | [tardis.dev](https://tardis.dev) |

Plus exchange-authenticated endpoints via CCXT: `FLINT_CCXT_API_KEY` + `FLINT_CCXT_SECRET`.

## Via `.env` (recommended — never commit)

Create `.env` in the project root:

```
FLINT_BIRDEYE_API_KEY=your_key_here
FLINT_HELIUS_API_KEY=your_key_here
```

`.gitignore` already excludes it.

## Via CLI

```bash
flint data provider enable birdeye --api-key $YOUR_KEY
```

Sets `providers.birdeye.enabled: true` in `flint.yaml` and appends the key to `.env`.

## Via env var (transient)

```bash
FLINT_BIRDEYE_API_KEY=your_key_here flint serve
```

## Verify

```bash
flint data provider status
```

Look for the `api_key` column — `required` means the provider is waiting for a key. After setting, `Available` should flip to `yes`.

Or via API:

```bash
curl -s localhost:8000/api/v1/data/providers | jq '.providers[] | select(.requires_api_key)'
```

## System endpoint (UI-visible)

The UI's Setup page calls `POST /api/v1/system/config` which appends to `.env`:

```bash
curl -X POST localhost:8000/api/v1/system/config \
  -H 'Content-Type: application/json' \
  -d '{"birdeye_api_key":"xxx","helius_api_key":"yyy"}'
```

## Rotate / revoke

Edit `.env` directly. Restart `flint serve` so the new key is picked up (config loads at boot).

## Gotchas

- **Don't put keys in `flint.yaml`** if the repo is on GitHub. Use `.env`.
- **Keys don't hot-reload.** Restart the server after changing.
- **Birdeye free tier rate-limits.** Heavy backfills will 429; back off or upgrade.

## Related

- [reference/config.md](../reference/config.md) — all config precedence rules
- [reference/data-providers.md](../reference/data-providers.md) — which providers need what
