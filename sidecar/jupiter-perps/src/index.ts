import Fastify from "fastify";
import { JupiterPerpsClient } from "./lib/client.js";
import { KeeperWatcher } from "./lib/keeper.js";
import { getOraclePrice } from "./lib/oracle.js";
import { registerPositionRoutes } from "./routes/positions.js";
import { registerOrderRoutes } from "./routes/orders.js";
import { registerAccountRoutes } from "./routes/account.js";

// ---- Configuration from environment ----

const PORT = parseInt(process.env.PORT ?? "8401", 10);
const HOST = "127.0.0.1";
const RPC_URL =
  process.env.FLINT_SOLANA_RPC ??
  process.env.SOLANA_RPC_URL ??
  "https://api.mainnet-beta.solana.com";
const WALLET_KEY = process.env.FLINT_WALLET_KEY; // base58-encoded private key (optional)

// ---- Fastify instance ----

const app = Fastify({ logger: true });

// ---- Shared singletons ----

const client = new JupiterPerpsClient({
  rpcUrl: RPC_URL,
  walletPrivateKey: WALLET_KEY,
});

const keeper = new KeeperWatcher({
  connection: client.connection,
  pollIntervalMs: parseInt(process.env.KEEPER_POLL_MS ?? "5000", 10),
  fulfillmentTimeoutMs: parseInt(
    process.env.KEEPER_TIMEOUT_MS ?? "60000",
    10
  ),
});

// ---- Core endpoints ----

/**
 * GET /health
 * Liveness check. Returns the configured RPC URL and wallet address (if loaded).
 */
app.get("/health", async (_req, reply) => {
  return reply.send({
    status: "ok",
    rpc: RPC_URL,
    wallet: client.walletAddress,
  });
});

/**
 * GET /oracle/:market
 * Returns the current oracle price and borrow rate for a market.
 *
 * Response shape:
 *   { market: string, price: number, borrow_rate: number, oracle_pubkey: string|null, slot: number|null }
 *
 * TODO: Price will be 0 until jup-perps-client oracle deserialization is integrated.
 */
app.get<{ Params: { market: string } }>(
  "/oracle/:market",
  async (req, reply) => {
    const { market } = req.params;
    const result = await getOraclePrice(client.connection, market);
    return reply.send(result);
  }
);

// ---- Route modules ----

registerPositionRoutes(app, client);
registerOrderRoutes(app, client, keeper);
registerAccountRoutes(app, client);

// ---- Startup & shutdown ----

async function start(): Promise<void> {
  try {
    keeper.start();
    await app.listen({ port: PORT, host: HOST });
    app.log.info(
      `Jupiter Perps sidecar listening on http://${HOST}:${PORT}`
    );
    app.log.info(`RPC: ${RPC_URL}`);
    app.log.info(
      `Wallet: ${client.walletAddress ?? "(no wallet key configured)"}`
    );
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}

process.on("SIGTERM", async () => {
  app.log.info("SIGTERM received — shutting down gracefully");
  keeper.stop();
  await app.close();
  process.exit(0);
});

process.on("SIGINT", async () => {
  app.log.info("SIGINT received — shutting down gracefully");
  keeper.stop();
  await app.close();
  process.exit(0);
});

void start();
