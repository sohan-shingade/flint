import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import type { JupiterPerpsClient } from "../lib/client.js";

interface MarketParams {
  market: string;
}

/**
 * Register position-related routes on the Fastify instance.
 *
 * GET /positions           — list all open positions for the connected wallet
 * GET /position/:market    — get the position for a specific market
 */
export function registerPositionRoutes(
  app: FastifyInstance,
  client: JupiterPerpsClient
): void {
  /**
   * GET /positions
   * Returns all open perpetual positions for the wallet configured in the sidecar.
   *
   * Response shape:
   *   { wallet: string, positions: PositionInfo[] }
   *
   * TODO: Replace stub with client.getPositions() once jup-perps-client is integrated.
   */
  app.get("/positions", async (_req: FastifyRequest, reply: FastifyReply) => {
    // TODO: integrate jup-perps-client — call client.getPositions()
    const positions = await client.getPositions();
    return reply.send({
      wallet: client.walletAddress,
      positions,
    });
  });

  /**
   * GET /position/:market
   * Returns the open position for a specific market (e.g. "SOL-PERP").
   *
   * Response shape (position found):
   *   { wallet: string, market: string, position: PositionInfo }
   *
   * Response shape (no position):
   *   { wallet: string, market: string, position: null }
   *
   * TODO: Replace stub with client.getPosition(market) once jup-perps-client is integrated.
   */
  app.get(
    "/position/:market",
    async (
      req: FastifyRequest<{ Params: MarketParams }>,
      reply: FastifyReply
    ) => {
      const { market } = req.params;
      // TODO: integrate jup-perps-client — call client.getPosition(market)
      const position = await client.getPosition(market);
      return reply.send({
        wallet: client.walletAddress,
        market,
        position,
      });
    }
  );
}
