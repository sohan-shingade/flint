import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import type { JupiterPerpsClient } from "../lib/client.js";

/**
 * Register account-related routes on the Fastify instance.
 *
 * GET /balances   — fetch collateral token balances for the connected wallet
 */
export function registerAccountRoutes(
  app: FastifyInstance,
  client: JupiterPerpsClient
): void {
  /**
   * GET /balances
   * Returns collateral token balances held by the wallet configured in the sidecar.
   * Relevant tokens include USDC, SOL, and any Jupiter Perps-supported collateral.
   *
   * Response shape:
   *   { wallet: string, balances: BalanceInfo[] }
   *
   * Where BalanceInfo is:
   *   { token: string, mint: string, amount: number, amount_usd: number }
   *
   * TODO: Replace stub with client.getBalances() once jup-perps-client is integrated.
   * The client will enumerate token accounts for the wallet and look up USD values
   * via on-chain oracle prices.
   */
  app.get("/balances", async (_req: FastifyRequest, reply: FastifyReply) => {
    // TODO: integrate jup-perps-client — call client.getBalances()
    const balances = await client.getBalances();
    return reply.send({
      wallet: client.walletAddress,
      balances,
    });
  });
}
