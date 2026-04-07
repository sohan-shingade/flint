import { Connection, PublicKey } from "@solana/web3.js";

/**
 * Known oracle account addresses for Jupiter Perps markets.
 * TODO: Replace with actual Pyth/Switchboard oracle pubkeys for each market
 * once jup-perps-client is integrated (client exposes these per-market).
 */
const ORACLE_ACCOUNTS: Record<string, string> = {
  "SOL-PERP": "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG",  // Pyth SOL/USD
  "BTC-PERP": "GVXRSBjFk6e6J3NbVPXohDJetcTjaeeuykUpbQF8UoMU",  // Pyth BTC/USD
  "ETH-PERP": "JBu1AL4obBcCMqKBBxhpWCNUt136ijcuMZLFvTP7iWdB",  // Pyth ETH/USD
  "WIF-PERP": "6ABgrEZkHbcBnMLHbZzNDeFHcGByGGGQnBrFfSjbMuEv",  // Pyth WIF/USD
  "BONK-PERP": "8ihFLu5FimgTQ1Unh4dVyEHUGodJ738bWMApoapSfBPo", // Pyth BONK/USD
};

export interface OraclePriceResult {
  market: string;
  price: number;
  borrow_rate: number;
  oracle_pubkey: string | null;
  slot: number | null;
}

/**
 * Reads the oracle price for a given market from on-chain Pyth/Switchboard accounts.
 * TODO: Integrate actual oracle account deserialization once jup-perps-client is
 * available — it exposes typed getOraclePrice(market) helpers.
 *
 * @param connection - Solana RPC connection
 * @param market - Market identifier, e.g. "SOL-PERP"
 */
export async function getOraclePrice(
  connection: Connection,
  market: string
): Promise<OraclePriceResult> {
  const oraclePubkeyStr = ORACLE_ACCOUNTS[market.toUpperCase()] ?? null;

  if (!oraclePubkeyStr) {
    return {
      market,
      price: 0,
      borrow_rate: 0,
      oracle_pubkey: null,
      slot: null,
    };
  }

  // TODO: Deserialize the Pyth price account to read the actual price.
  // Pseudocode for when jup-perps-client is integrated:
  //   const oraclePubkey = new PublicKey(oraclePubkeyStr);
  //   const accountInfo = await connection.getAccountInfo(oraclePubkey);
  //   const priceData = parsePythPriceAccount(accountInfo);
  //   return { market, price: priceData.price, borrow_rate: 0, ... };

  return {
    market,
    price: 0,
    borrow_rate: 0,
    oracle_pubkey: oraclePubkeyStr,
    slot: null,
  };
}

/**
 * Returns all markets that have known oracle accounts configured.
 */
export function listSupportedMarkets(): string[] {
  return Object.keys(ORACLE_ACCOUNTS);
}
