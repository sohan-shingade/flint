"""Multi-hop arbitrage detection across AMM pools.

Builds a directed graph of pools and detects negative-weight cycles
(profitable arbitrage routes) using the Bellman-Ford algorithm.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from ..models import ArbRoute, PoolState

if TYPE_CHECKING:
    from ..mev.clmm import CLMMPool


@dataclass
class _Edge:
    """Directed edge: swap token_in → token_out through a pool."""
    pool_address: str
    token_in: str
    token_out: str
    reserve_in: float
    reserve_out: float
    fee_rate: float
    clmm_pool: Optional["CLMMPool"] = field(default=None, compare=False)
    is_a_to_b: bool = True

    def output_amount(self, amount_in: float) -> float:
        """Delegate to CLMM tick-walking if available, else constant-product AMM."""
        if self.clmm_pool is not None:
            return self.clmm_pool.output_amount(amount_in, self.is_a_to_b)
        effective_in = amount_in * (1 - self.fee_rate)
        return (self.reserve_out * effective_in) / (self.reserve_in + effective_in)

    def effective_price(self, amount_in: float = 1.0) -> float:
        """Price including fee and slippage for given input."""
        out = self.output_amount(amount_in)
        return out / amount_in if amount_in > 0 else 0.0


class ArbDetector:
    """Detects arbitrage opportunities across a set of AMM pools.

    Usage::

        detector = ArbDetector()
        detector.update_pools([pool1, pool2, pool3])
        routes = detector.find_arb_routes("So11...112", amount=1.0)
    """

    def __init__(self, min_profit_bps: float = 10.0) -> None:
        self.min_profit_bps = min_profit_bps
        self._edges: List[_Edge] = []
        self._tokens: Set[str] = set()
        self._adjacency: Dict[str, List[_Edge]] = defaultdict(list)

    def update_pools(self, pools: List[PoolState], clmm_pools: Optional[Dict[str, "CLMMPool"]] = None) -> None:
        """Rebuild the graph from current pool states.

        Args:
            pools: List of PoolState objects (constant-product or CLMM).
            clmm_pools: Optional mapping of pool_address → CLMMPool. When provided,
                edges for matching pools delegate output_amount() to the CLMM model.
        """
        self._edges.clear()
        self._tokens.clear()
        self._adjacency.clear()

        for p in pools:
            self._tokens.add(p.token_a_mint)
            self._tokens.add(p.token_b_mint)

            clmm = clmm_pools.get(p.pool_address) if clmm_pools else None

            # Forward: A → B
            fwd = _Edge(p.pool_address, p.token_a_mint, p.token_b_mint,
                        p.reserve_a, p.reserve_b, p.fee_rate,
                        clmm_pool=clmm, is_a_to_b=True)
            # Reverse: B → A
            rev = _Edge(p.pool_address, p.token_b_mint, p.token_a_mint,
                        p.reserve_b, p.reserve_a, p.fee_rate,
                        clmm_pool=clmm, is_a_to_b=False)

            self._edges.extend([fwd, rev])
            self._adjacency[p.token_a_mint].append(fwd)
            self._adjacency[p.token_b_mint].append(rev)

    def find_arb_routes(
        self,
        start_token: str,
        amount: float = 1.0,
        max_hops: int = 4,
    ) -> List[ArbRoute]:
        """Find profitable circular routes starting and ending at ``start_token``.

        Uses DFS with pruning to explore paths up to ``max_hops`` deep.
        """
        routes: List[ArbRoute] = []
        self._dfs(
            start_token=start_token,
            current_token=start_token,
            current_amount=amount,
            initial_amount=amount,
            path_pools=[],
            path_tokens=[start_token],
            visited_pools=set(),
            max_hops=max_hops,
            routes=routes,
        )
        routes.sort(key=lambda r: r.profit_bps, reverse=True)
        return routes

    def _dfs(
        self,
        start_token: str,
        current_token: str,
        current_amount: float,
        initial_amount: float,
        path_pools: List[str],
        path_tokens: List[str],
        visited_pools: Set[str],
        max_hops: int,
        routes: List[ArbRoute],
    ) -> None:
        if len(path_pools) > max_hops:
            return

        # Check if we've completed a cycle
        if len(path_pools) >= 2 and current_token == start_token:
            profit = current_amount - initial_amount
            profit_bps = (profit / initial_amount) * 10_000 if initial_amount else 0
            if profit_bps >= self.min_profit_bps:
                routes.append(ArbRoute(
                    pools=tuple(path_pools),
                    tokens=tuple(path_tokens),
                    input_amount=initial_amount,
                    output_amount=current_amount,
                    profit=profit,
                    profit_bps=profit_bps,
                ))
            return

        for edge in self._adjacency.get(current_token, []):
            if edge.pool_address in visited_pools:
                continue
            out = edge.output_amount(current_amount)
            if out <= 0:
                continue
            visited_pools.add(edge.pool_address)
            path_pools.append(edge.pool_address)
            path_tokens.append(edge.token_out)
            self._dfs(
                start_token, edge.token_out, out, initial_amount,
                path_pools, path_tokens, visited_pools, max_hops, routes,
            )
            path_pools.pop()
            path_tokens.pop()
            visited_pools.discard(edge.pool_address)

    def estimate_profit(self, route: ArbRoute, amount: float) -> float:
        """Re-simulate a known route with a different input amount."""
        current = amount
        pool_map = {e.pool_address + e.token_in: e for e in self._edges}
        for i, pool_addr in enumerate(route.pools):
            token_in = route.tokens[i]
            key = pool_addr + token_in
            edge = pool_map.get(key)
            if edge is None:
                return 0.0
            current = edge.output_amount(current)
        return current - amount
