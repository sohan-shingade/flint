import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import type {
  JupiterPerpsClient,
  IncreasePositionParams,
  DecreasePositionParams,
  ClosePositionParams,
} from "../lib/client.js";
import type { KeeperWatcher } from "../lib/keeper.js";

/**
 * Register order-related routes on the Fastify instance.
 *
 * POST /increase   — open or increase a position
 * POST /decrease   — partially close a position
 * POST /close      — fully close a position
 */
export function registerOrderRoutes(
  app: FastifyInstance,
  client: JupiterPerpsClient,
  keeper: KeeperWatcher
): void {
  /**
   * POST /increase
   * Open a new position or add to an existing one.
   *
   * Request body:
   *   { market: string, side: "long"|"short", collateral_usd: number, leverage: number, slippage_bps?: number }
   *
   * Response shape:
   *   { signature: string, status: "pending", market: string }
   *
   * TODO: Replace stub with client.increasePosition(body) once jup-perps-client is integrated.
   * The response signature should then be passed to keeper.watch(signature, market).
   */
  app.post(
    "/increase",
    async (
      req: FastifyRequest<{ Body: IncreasePositionParams }>,
      reply: FastifyReply
    ) => {
      const body = req.body;
      // TODO: integrate jup-perps-client — build + send increasePosition tx
      // const signature = await client.increasePosition(body);
      // keeper.watch(signature, body.market);
      // return reply.code(202).send({ signature, status: "pending", market: body.market });

      // Stub response until jup-perps-client is integrated
      return reply.code(501).send({
        error: "Not implemented",
        detail:
          "jup-perps-client integration pending — POST /increase is a scaffold stub",
        received: body,
      });
    }
  );

  /**
   * POST /decrease
   * Partially reduce an existing position.
   *
   * Request body:
   *   { market: string, side: "long"|"short", size_usd: number, slippage_bps?: number }
   *
   * Response shape:
   *   { signature: string, status: "pending", market: string }
   *
   * TODO: Replace stub with client.decreasePosition(body) once jup-perps-client is integrated.
   */
  app.post(
    "/decrease",
    async (
      req: FastifyRequest<{ Body: DecreasePositionParams }>,
      reply: FastifyReply
    ) => {
      const body = req.body;
      // TODO: integrate jup-perps-client — build + send decreasePosition tx
      // const signature = await client.decreasePosition(body);
      // keeper.watch(signature, body.market);
      // return reply.code(202).send({ signature, status: "pending", market: body.market });

      return reply.code(501).send({
        error: "Not implemented",
        detail:
          "jup-perps-client integration pending — POST /decrease is a scaffold stub",
        received: body,
      });
    }
  );

  /**
   * POST /close
   * Fully close an existing position.
   *
   * Request body:
   *   { market: string, side: "long"|"short", slippage_bps?: number }
   *
   * Response shape:
   *   { signature: string, status: "pending", market: string }
   *
   * TODO: Replace stub with client.closePosition(body) once jup-perps-client is integrated.
   */
  app.post(
    "/close",
    async (
      req: FastifyRequest<{ Body: ClosePositionParams }>,
      reply: FastifyReply
    ) => {
      const body = req.body;
      // TODO: integrate jup-perps-client — build + send closePosition tx
      // const signature = await client.closePosition(body);
      // keeper.watch(signature, body.market);
      // return reply.code(202).send({ signature, status: "pending", market: body.market });

      return reply.code(501).send({
        error: "Not implemented",
        detail:
          "jup-perps-client integration pending — POST /close is a scaffold stub",
        received: body,
      });
    }
  );
}
