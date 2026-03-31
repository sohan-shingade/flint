"""Tests for WalletAdapter — mocked, no real Solana signing."""
import pytest
from unittest.mock import patch, MagicMock


class TestKeypairAdapter:
    def test_create_from_env(self, monkeypatch):
        # Use a valid base58 keypair (64 bytes encoded)
        fake_key = "4wBqpZM9k69W87zdYRzM2FYF9czGSGarfKfabkFtEfGHiKA4VEbJNFMZ1eKQxZNrFBQTnJsEbYBThG8X8DSGA6DD"
        monkeypatch.setenv("FLINT_PRIVATE_KEY", fake_key)
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter()
        assert adapter.public_key is not None

    def test_create_from_param(self):
        fake_key = "4wBqpZM9k69W87zdYRzM2FYF9czGSGarfKfabkFtEfGHiKA4VEbJNFMZ1eKQxZNrFBQTnJsEbYBThG8X8DSGA6DD"
        from flint.execution.wallet import KeypairAdapter
        adapter = KeypairAdapter(private_key=fake_key)
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
