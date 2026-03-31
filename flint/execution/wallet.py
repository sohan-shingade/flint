"""WalletAdapter — signing abstraction for live trading.

Decouples transaction signing from venue execution so different
wallet backends (local keypair, browser extension) can be swapped.
"""
from __future__ import annotations

import abc
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("flint.wallet")


class WalletAdapter(abc.ABC):
    """Abstract base for transaction signing."""

    @abc.abstractmethod
    async def sign_and_send(self, tx, connection) -> str:
        """Sign a transaction and send it. Returns tx signature string."""
        ...

    @property
    @abc.abstractmethod
    def public_key(self):
        """Return the wallet's public key."""
        ...


class KeypairAdapter(WalletAdapter):
    """Signs transactions locally using a base58-encoded private key.

    Key source (in priority order):
    1. private_key parameter
    2. FLINT_PRIVATE_KEY environment variable
    """

    def __init__(self, private_key: str | None = None):
        from solders.keypair import Keypair  # type: ignore

        key = private_key or os.environ.get("FLINT_PRIVATE_KEY", "")
        if not key:
            raise ValueError(
                "No private key provided. Set FLINT_PRIVATE_KEY env var "
                "or pass private_key parameter."
            )

        self._keypair = Keypair.from_base58_string(key)
        logger.info("KeypairAdapter initialized (pubkey: %s)", self._keypair.pubkey())

    @property
    def public_key(self):
        return self._keypair.pubkey()

    @property
    def keypair(self):
        """Access the underlying Keypair for driftpy DriftClient."""
        return self._keypair

    async def sign_and_send(self, tx, connection) -> str:
        """Sign and send a transaction via the RPC connection."""
        result = await connection.send_transaction(tx, self._keypair)
        return str(result.value)


class BrowserWalletAdapter(WalletAdapter):
    """Placeholder for browser extension wallet signing (Phantom, Brave, etc.).

    Implementation deferred — will relay unsigned transactions to the React UI
    via WebSocket for signing with @solana/wallet-adapter.
    Requires UI to be open; cannot run unattended.
    """

    async def sign_and_send(self, tx, connection) -> str:
        raise NotImplementedError(
            "BrowserWalletAdapter is not yet implemented. "
            "Use KeypairAdapter with FLINT_PRIVATE_KEY for now."
        )

    @property
    def public_key(self):
        raise NotImplementedError("BrowserWalletAdapter is not yet implemented.")
