import { Connection, Keypair, PublicKey } from "@solana/web3.js";
import bs58 from "bs58";

export interface JupiterPerpsClientConfig {
  rpcUrl: string;
  walletPrivateKey?: string;
}

/**
 * JupiterPerpsClient wraps the jup-perps-client SDK.
 * TODO: Replace stub methods with actual jup-perps-client calls once integrated.
 */
export class JupiterPerpsClient {
  readonly connection: Connection;
  readonly wallet: Keypair | null;
  readonly rpcUrl: string;

  constructor(config: JupiterPerpsClientConfig) {
    this.rpcUrl = config.rpcUrl;
    this.connection = new Connection(config.rpcUrl, "confirmed");

    if (config.walletPrivateKey) {
      const secretKey = bs58.decode(config.walletPrivateKey);
      this.wallet = Keypair.fromSecretKey(secretKey);
    } else {
      this.wallet = null;
    }
  }

  get walletAddress(): string | null {
    return this.wallet ? this.wallet.publicKey.toBase58() : null;
  }

  /**
   * TODO: Fetch all open perpetual positions for the connected wallet.
   * Will call jup-perps-client.getPositions(wallet.publicKey) once integrated.
   */
  async getPositions(): Promise<PositionInfo[]> {
    // TODO: integrate jup-perps-client getPositions
    return [];
  }

  /**
   * TODO: Fetch a single position for a specific market.
   * Will call jup-perps-client.getPosition(wallet.publicKey, market) once integrated.
   */
  async getPosition(market: string): Promise<PositionInfo | null> {
    // TODO: integrate jup-perps-client getPosition
    return null;
  }

  /**
   * TODO: Build and send an increase-position transaction.
   * Will call jup-perps-client.increasePosition(...) once integrated.
   */
  async increasePosition(params: IncreasePositionParams): Promise<string> {
    // TODO: integrate jup-perps-client increasePosition
    throw new Error("Not implemented: jup-perps-client not yet integrated");
  }

  /**
   * TODO: Build and send a decrease-position transaction.
   * Will call jup-perps-client.decreasePosition(...) once integrated.
   */
  async decreasePosition(params: DecreasePositionParams): Promise<string> {
    // TODO: integrate jup-perps-client decreasePosition
    throw new Error("Not implemented: jup-perps-client not yet integrated");
  }

  /**
   * TODO: Build and send a close-position transaction.
   * Will call jup-perps-client.closePosition(...) once integrated.
   */
  async closePosition(params: ClosePositionParams): Promise<string> {
    // TODO: integrate jup-perps-client closePosition
    throw new Error("Not implemented: jup-perps-client not yet integrated");
  }

  /**
   * TODO: Fetch collateral balances for the connected wallet.
   * Will call jup-perps-client.getBalances(wallet.publicKey) once integrated.
   */
  async getBalances(): Promise<BalanceInfo[]> {
    // TODO: integrate jup-perps-client getBalances
    return [];
  }
}

// ---- Shared types ----

export interface PositionInfo {
  market: string;
  side: "long" | "short";
  size_usd: number;
  collateral_usd: number;
  entry_price: number;
  current_price: number;
  pnl_usd: number;
  leverage: number;
  liquidation_price: number;
}

export interface IncreasePositionParams {
  market: string;
  side: "long" | "short";
  collateral_usd: number;
  leverage: number;
  slippage_bps?: number;
}

export interface DecreasePositionParams {
  market: string;
  side: "long" | "short";
  size_usd: number;
  slippage_bps?: number;
}

export interface ClosePositionParams {
  market: string;
  side: "long" | "short";
  slippage_bps?: number;
}

export interface BalanceInfo {
  token: string;
  mint: string;
  amount: number;
  amount_usd: number;
}
