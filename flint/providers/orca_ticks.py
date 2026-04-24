"""OrcaTickFetcher — fetch Orca Whirlpool tick data from Solana RPC."""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

import logging
import math
from typing import Dict, List, Optional

from ..mev.clmm import CLMMPool, TickRange

logger = logging.getLogger("flint.orca_ticks")

WHIRLPOOL_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
TICK_ARRAY_SIZE = 88


class OrcaTickFetcher:
    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self._rpc_url = rpc_url

    async def fetch_pool(self, pool_address: str) -> Optional[CLMMPool]:
        """Fetch whirlpool state + tick arrays from on-chain."""
        try:
            from solana.rpc.async_api import AsyncClient
            from solders.pubkey import Pubkey
            async with AsyncClient(self._rpc_url) as client:
                pool_pubkey = Pubkey.from_string(pool_address)
                resp = await client.get_account_info(pool_pubkey)
                if resp.value is None:
                    return None
                whirlpool_data = self._decode_whirlpool(resp.value.data)
                if whirlpool_data is None:
                    return None
                current_tick = whirlpool_data["tick_current_index"]
                tick_spacing = whirlpool_data["tick_spacing"]
                pda_addresses = self._derive_tick_array_pdas(pool_address, current_tick, tick_spacing)
                tick_ranges = []
                for pda in pda_addresses:
                    try:
                        pda_pubkey = Pubkey.from_string(pda)
                        pda_resp = await client.get_account_info(pda_pubkey)
                        if pda_resp.value is not None:
                            raw_ticks = self._decode_tick_array(pda_resp.value.data)
                            tick_ranges.extend(self._ticks_to_ranges(raw_ticks, tick_spacing))
                    except Exception as e:
                        logger.debug("Failed to decode tick array %s: %s", pda, e)
                return self._build_pool(pool_address, whirlpool_data, tick_ranges)
        except ImportError:
            logger.error("solana/solders required for OrcaTickFetcher")
            return None
        except Exception as e:
            logger.error("Failed to fetch pool %s: %s", pool_address, e)
            return None

    async def fetch_pools(self, pool_addresses: List[str]) -> List[CLMMPool]:
        import asyncio
        results = await asyncio.gather(*[self.fetch_pool(a) for a in pool_addresses], return_exceptions=True)
        return [r for r in results if isinstance(r, CLMMPool)]

    def _build_pool(self, pool_address: str, whirlpool_data: dict, tick_ranges: List[TickRange]) -> CLMMPool:
        fee_rate = whirlpool_data.get("fee_rate", 0) / 1_000_000
        raw_sqrt_price = whirlpool_data.get("sqrt_price", 2**64)
        sqrt_price = raw_sqrt_price / (2**64)
        return CLMMPool(
            pool_address=pool_address, dex="orca",
            token_a_mint=str(whirlpool_data.get("token_mint_a", "")),
            token_b_mint=str(whirlpool_data.get("token_mint_b", "")),
            tick_ranges=tick_ranges,
            current_tick=whirlpool_data.get("tick_current_index", 0),
            tick_spacing=whirlpool_data.get("tick_spacing", 64),
            fee_rate=fee_rate, sqrt_price=sqrt_price,
        )

    def _decode_whirlpool(self, data) -> Optional[dict]:
        try:
            raw = bytes(data)
            if len(raw) < 300:
                return None
            import struct
            offset = 8 + 33
            tick_spacing = struct.unpack_from("<H", raw, offset)[0]; offset += 4
            fee_rate = struct.unpack_from("<H", raw, offset)[0]; offset += 4
            offset += 16  # liquidity
            sqrt_price = int.from_bytes(raw[offset:offset+16], "little"); offset += 16
            tick_current_index = struct.unpack_from("<i", raw, offset)[0]; offset += 4
            offset += 16  # protocol fees
            token_mint_a = raw[offset:offset+32]; offset += 32
            token_mint_b = raw[offset:offset+32]; offset += 32
            from solders.pubkey import Pubkey
            return {
                "tick_spacing": tick_spacing, "fee_rate": fee_rate,
                "sqrt_price": sqrt_price, "tick_current_index": tick_current_index,
                "token_mint_a": str(Pubkey.from_bytes(token_mint_a)),
                "token_mint_b": str(Pubkey.from_bytes(token_mint_b)),
            }
        except Exception as e:
            logger.error("Failed to decode whirlpool: %s", e)
            return None

    def _decode_tick_array(self, data) -> List[dict]:
        ticks = []
        try:
            raw = bytes(data)
            if len(raw) < 100:
                return []
            import struct
            offset = 8
            start_tick_index = struct.unpack_from("<i", raw, offset)[0]; offset += 4
            tick_size = 137
            for i in range(TICK_ARRAY_SIZE):
                tick_offset = offset + i * tick_size
                if tick_offset + 33 > len(raw):
                    break
                initialized = raw[tick_offset] != 0
                if not initialized:
                    continue
                liquidity_net = int.from_bytes(raw[tick_offset+1:tick_offset+17], "little", signed=True)
                liquidity_gross = int.from_bytes(raw[tick_offset+17:tick_offset+33], "little", signed=False)
                ticks.append({"tick_index": start_tick_index + i, "liquidity_net": liquidity_net,
                    "liquidity_gross": liquidity_gross, "initialized": True})
        except Exception as e:
            logger.debug("Failed to decode tick array: %s", e)
        return ticks

    def _ticks_to_ranges(self, raw_ticks: List[dict], tick_spacing: int) -> List[TickRange]:
        if not raw_ticks:
            return []
        sorted_ticks = sorted(raw_ticks, key=lambda t: t["tick_index"])
        ranges = []
        current_liquidity = 0.0
        prev_tick = None
        for tick in sorted_ticks:
            if prev_tick is not None and current_liquidity > 0:
                ranges.append(TickRange(tick_lower=prev_tick, tick_upper=tick["tick_index"], liquidity=current_liquidity))
            current_liquidity += tick["liquidity_net"]
            prev_tick = tick["tick_index"]
        return ranges

    def _derive_tick_array_pdas(self, pool_address: str, current_tick: int, tick_spacing: int, count: int = 3) -> List[str]:
        pdas = []
        try:
            from solders.pubkey import Pubkey
            import struct
            program_id = Pubkey.from_string(WHIRLPOOL_PROGRAM_ID)
            pool_pubkey = Pubkey.from_string(pool_address)
            ticks_per_array = TICK_ARRAY_SIZE * tick_spacing
            if ticks_per_array <= 0:
                return []
            start_index = (current_tick // ticks_per_array) * ticks_per_array
            for offset in range(-count, count + 1):
                idx = start_index + offset * ticks_per_array
                try:
                    seeds = [b"tick_array", bytes(pool_pubkey), struct.pack("<i", idx)]
                    pda, _ = Pubkey.find_program_address(seeds, program_id)
                    pdas.append(str(pda))
                except Exception:
                    continue
        except ImportError:
            pass
        return pdas
