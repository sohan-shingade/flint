# How to: Add API keys

Most market-data paths need no key. Tardis is an optional BYO-license backfill
source for historical Hyperliquid tick and order-book data.

| Provider | Env var | Access | Sign-up |
|---|---|---|---|
| Tardis | `TARDIS_API_KEY` | paid | [tardis.dev](https://tardis.dev) |

## Via `.env` (recommended — never commit)

Create `.env` in the project directory. Flint searches from the directory where
it is launched upward to the nearest `.env`:

```dotenv
TARDIS_API_KEY=your_key_here
```

`.gitignore` already excludes it. Flint loads the file when it constructs its
local secrets adapter, so `source .env` is not required. An exported environment
variable with the same name takes precedence over the file.

## Via env var (transient)

```bash
TARDIS_API_KEY=your_key_here flint serve
```

## Verify

A configured key makes Tardis available as the paid backfill lane when Flint's
local data manager is assembled with the Tardis tier. Authentication is exercised
only by a request for a non-first-of-month dataset; Tardis makes first-of-month
files public.

## Rotate / revoke

Edit `.env` directly and restart Flint. Keys do not hot-reload.

## Gotchas

- **Don't put keys in tracked configuration files.** Use `.env`.
- **Exported variables win over `.env`.** Unset the variable if a file edit seems ignored.
- **Strategy environments are scrubbed.** Keep the project and `.env` readable only by trusted users.

## Related

- [reference/config.md](../reference/config.md) — configuration precedence
- [reference/data-providers.md](../reference/data-providers.md) — data-provider behavior
