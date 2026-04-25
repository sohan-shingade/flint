"""Tests for WalletAdapter — mocked, no real Solana signing."""
import pytest


def _generate_valid_key() -> str:
    """Generate a valid Solana keypair and return its base58 string."""
    from solders.keypair import Keypair  # type: ignore
    return str(Keypair())


class TestKeypairAdapter:
    def test_create_from_env(self, monkeypatch):
        valid_key = _generate_valid_key()
        monkeypatch.setenv("FLINT_PRIVATE_KEY", valid_key)
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter()
        assert adapter.public_key is not None

    def test_create_from_param(self):
        valid_key = _generate_valid_key()
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter(private_key=valid_key)
        assert adapter.public_key is not None

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLINT_PRIVATE_KEY", raising=False)
        from flint.execution.wallet import KeypairAdapter
        with pytest.raises(ValueError, match="No private key"):
            KeypairAdapter()

    def test_invalid_key_raises(self):
        from flint.execution.wallet import KeypairAdapter
        with pytest.raises(BaseException):
            KeypairAdapter(private_key="not-a-valid-key")


class TestBrowserWalletAdapter:
    def test_interface_defined(self):
        from flint.execution.wallet import BrowserWalletAdapter
        assert hasattr(BrowserWalletAdapter, "sign_and_send")
        assert hasattr(BrowserWalletAdapter, "public_key")
