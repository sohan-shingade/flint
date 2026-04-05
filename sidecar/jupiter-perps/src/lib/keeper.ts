import { Connection } from "@solana/web3.js";

export interface KeeperWatcherConfig {
  connection: Connection;
  /** How often to poll for unfulfilled orders (milliseconds). Default: 5000 */
  pollIntervalMs?: number;
  /** How long to wait for an order to be fulfilled before timing out (milliseconds). Default: 60000 */
  fulfillmentTimeoutMs?: number;
}

export type OrderStatus = "pending" | "fulfilled" | "cancelled" | "timeout";

export interface WatchedOrder {
  signature: string;
  market: string;
  submittedAt: Date;
  status: OrderStatus;
  fulfilledAt?: Date;
  error?: string;
}

/**
 * KeeperWatcher polls the chain for Jupiter Perps keeper-fulfilled orders.
 *
 * Jupiter Perps uses a keeper model: user transactions create "request" accounts
 * on-chain; keepers watch for these and submit fulfillment transactions.
 * This watcher polls transaction status to detect fulfillment or timeout.
 *
 * TODO: Once jup-perps-client is integrated, replace the polling loop with
 * jup-perps-client.watchOrderFulfillment(signature) which subscribes to
 * the request account and resolves when fulfilled.
 */
export class KeeperWatcher {
  private readonly connection: Connection;
  private readonly pollIntervalMs: number;
  private readonly fulfillmentTimeoutMs: number;
  private readonly watched = new Map<string, WatchedOrder>();
  private pollHandle: ReturnType<typeof setTimeout> | null = null;
  private running = false;

  constructor(config: KeeperWatcherConfig) {
    this.connection = config.connection;
    this.pollIntervalMs = config.pollIntervalMs ?? 5_000;
    this.fulfillmentTimeoutMs = config.fulfillmentTimeoutMs ?? 60_000;
  }

  /**
   * Start the background polling loop.
   */
  start(): void {
    if (this.running) return;
    this.running = true;
    this.schedulePoll();
  }

  /**
   * Stop the background polling loop.
   */
  stop(): void {
    this.running = false;
    if (this.pollHandle !== null) {
      clearTimeout(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /**
   * Add an order signature to watch for keeper fulfillment.
   * Returns the WatchedOrder record.
   */
  watch(signature: string, market: string): WatchedOrder {
    const order: WatchedOrder = {
      signature,
      market,
      submittedAt: new Date(),
      status: "pending",
    };
    this.watched.set(signature, order);
    return order;
  }

  /**
   * Get the current status of a watched order.
   */
  getStatus(signature: string): WatchedOrder | null {
    return this.watched.get(signature) ?? null;
  }

  /**
   * Return all currently watched orders.
   */
  getAllOrders(): WatchedOrder[] {
    return Array.from(this.watched.values());
  }

  private schedulePoll(): void {
    if (!this.running) return;
    this.pollHandle = setTimeout(() => {
      void this.poll().finally(() => this.schedulePoll());
    }, this.pollIntervalMs);
  }

  /**
   * Poll on-chain for the status of each pending order.
   * TODO: Replace with jup-perps-client request-account subscription
   * for event-driven fulfillment detection instead of polling.
   */
  private async poll(): Promise<void> {
    const now = Date.now();

    for (const [signature, order] of this.watched) {
      if (order.status !== "pending") continue;

      const elapsed = now - order.submittedAt.getTime();
      if (elapsed >= this.fulfillmentTimeoutMs) {
        order.status = "timeout";
        order.error = `Order not fulfilled within ${this.fulfillmentTimeoutMs}ms`;
        continue;
      }

      try {
        // TODO: Once jup-perps-client is available, check the request account
        // rather than just the transaction status. The transaction confirming
        // only means the request was submitted; the keeper still needs to fulfill.
        const result = await this.connection.getSignatureStatus(signature, {
          searchTransactionHistory: true,
        });

        if (result.value?.confirmationStatus === "finalized") {
          if (result.value.err) {
            order.status = "cancelled";
            order.error = JSON.stringify(result.value.err);
          } else {
            // TODO: Also verify the keeper fulfillment transaction here.
            order.status = "fulfilled";
            order.fulfilledAt = new Date();
          }
        }
      } catch (err) {
        // Non-fatal: will retry on next poll
        const message = err instanceof Error ? err.message : String(err);
        console.warn(`[KeeperWatcher] Failed to check ${signature}: ${message}`);
      }
    }

    // Prune orders that are no longer pending and are older than 10 minutes
    const pruneBeforeMs = now - 10 * 60 * 1_000;
    for (const [signature, order] of this.watched) {
      if (
        order.status !== "pending" &&
        order.submittedAt.getTime() < pruneBeforeMs
      ) {
        this.watched.delete(signature);
      }
    }
  }
}
