"""Tests for the JupiterSidecar process manager."""
from unittest.mock import MagicMock, patch

from flint.execution.jupiter_sidecar import JupiterSidecar


def test_sidecar_init():
    s = JupiterSidecar(port=8401)
    assert s.port == 8401
    assert s.is_running is False
    assert s._max_restarts == 3
    assert s._restart_count == 0


def test_sidecar_init_custom_params():
    s = JupiterSidecar(port=9000, rpc_url="https://rpc.example.com",
                       wallet_path="/tmp/wallet.json", max_restarts=5)
    assert s.port == 9000
    assert s._rpc_url == "https://rpc.example.com"
    assert s._wallet_path == "/tmp/wallet.json"
    assert s._max_restarts == 5


def test_base_url():
    s = JupiterSidecar(port=8401)
    assert s.base_url == "http://127.0.0.1:8401"


def test_check_node_available():
    s = JupiterSidecar(port=8401)
    with patch("shutil.which", return_value="/usr/local/bin/node"):
        assert s._check_node() is True
    with patch("shutil.which", return_value=None):
        assert s._check_node() is False


def test_health_check_success():
    s = JupiterSidecar(port=8401)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.get", return_value=mock_resp):
        assert s._health_check() is True


def test_health_check_failure():
    s = JupiterSidecar(port=8401)
    with patch("httpx.get", side_effect=Exception("Connection refused")):
        assert s._health_check() is False


def test_health_check_non_200():
    s = JupiterSidecar(port=8401)
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("httpx.get", return_value=mock_resp):
        assert s._health_check() is False


def test_max_restarts():
    s = JupiterSidecar(port=8401, max_restarts=3)
    assert s._max_restarts == 3
    assert s._restart_count == 0


def test_start_no_node():
    """start() returns False when Node.js is not on PATH."""
    s = JupiterSidecar(port=8401)
    with patch("shutil.which", return_value=None):
        result = s.start()
    assert result is False
    assert s.is_running is False


def test_start_no_sidecar_dir():
    """start() returns False when sidecar directory does not exist."""
    s = JupiterSidecar(port=8401)
    with patch("shutil.which", return_value="/usr/bin/node"), \
         patch("flint.execution.jupiter_sidecar._SIDECAR_DIR") as mock_dir:
        mock_dir.exists.return_value = False
        result = s.start()
    assert result is False


def test_stop_when_not_running():
    """stop() is safe to call even when process is None."""
    s = JupiterSidecar(port=8401)
    s.stop()  # should not raise
    assert s._process is None


def test_is_running_false_when_process_exited():
    """is_running returns False when process has exited (poll() returns non-None)."""
    s = JupiterSidecar(port=8401)
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0  # process has exited
    s._process = mock_proc
    assert s.is_running is False


def test_is_running_true_when_process_alive():
    """is_running returns True when process is alive (poll() returns None)."""
    s = JupiterSidecar(port=8401)
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # still running
    s._process = mock_proc
    assert s.is_running is True
