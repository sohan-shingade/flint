"""Tests for flint calibrate CLI command."""
import re
from typer.testing import CliRunner
from flint.cli import app

def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

class TestCalibrateCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        assert result.exit_code == 0
        assert "venue" in _strip_ansi(result.output).lower()

    def test_dry_run_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        clean = _strip_ansi(result.output).lower()
        assert "dry-run" in clean or "dry_run" in clean
