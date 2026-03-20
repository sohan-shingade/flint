"""Jito bundle builder for MEV transaction submission.

Constructs bundles of Solana instructions that get submitted atomically
via Jito's block engine. In the current phase this builds the bundle
representation — actual submission requires a Jito connection and wallet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Instruction:
    """A single Solana instruction (simplified representation)."""
    program_id: str
    accounts: List[str]  # pubkeys involved
    data: bytes = b""

    def size_bytes(self) -> int:
        return len(self.data) + len(self.accounts) * 32 + 32


@dataclass
class Bundle:
    """A Jito bundle — a sequence of transactions submitted atomically."""
    instructions: List[Instruction] = field(default_factory=list)
    tip_lamports: int = 0
    max_tip_lamports: int = 10_000  # 0.00001 SOL default max
    signer: str = ""  # wallet pubkey

    @property
    def num_instructions(self) -> int:
        return len(self.instructions)

    @property
    def estimated_size(self) -> int:
        return sum(ix.size_bytes() for ix in self.instructions)

    @property
    def tip_sol(self) -> float:
        return self.tip_lamports / 1_000_000_000


# Solana transaction size limit
_MAX_TX_SIZE = 1232  # bytes


class BundleBuilder:
    """Builds Jito bundles from a series of instructions.

    Usage::

        builder = BundleBuilder(signer="MyWallet...")
        builder.add_instruction(swap_ix)
        builder.add_instruction(arb_ix)
        builder.set_tip(1000)  # lamports
        bundle = builder.build()

        if builder.validate(bundle):
            # submit via Jito
            ...
    """

    def __init__(self, signer: str = "", default_tip: int = 1000) -> None:
        self._signer = signer
        self._default_tip = default_tip
        self._instructions: List[Instruction] = []

    def add_instruction(self, ix: Instruction) -> "BundleBuilder":
        self._instructions.append(ix)
        return self

    def clear(self) -> "BundleBuilder":
        self._instructions.clear()
        return self

    def build(self, tip_lamports: Optional[int] = None) -> Bundle:
        tip = tip_lamports if tip_lamports is not None else self._default_tip
        return Bundle(
            instructions=list(self._instructions),
            tip_lamports=tip,
            signer=self._signer,
        )

    @staticmethod
    def validate(bundle: Bundle) -> List[str]:
        """Validate a bundle. Returns list of error strings (empty = valid)."""
        errors: List[str] = []
        if not bundle.instructions:
            errors.append("Bundle has no instructions")
        if bundle.estimated_size > _MAX_TX_SIZE:
            errors.append(
                f"Bundle size {bundle.estimated_size} exceeds "
                f"max tx size {_MAX_TX_SIZE}"
            )
        if bundle.tip_lamports < 0:
            errors.append("Tip cannot be negative")
        if bundle.tip_lamports > bundle.max_tip_lamports:
            errors.append(
                f"Tip {bundle.tip_lamports} exceeds max {bundle.max_tip_lamports}"
            )
        return errors

    @staticmethod
    def estimate_tip(
        expected_profit_lamports: int,
        tip_fraction: float = 0.5,
    ) -> int:
        """Suggest a tip as a fraction of expected profit."""
        return max(0, int(expected_profit_lamports * tip_fraction))
