"""Tests for flint calibrate CLI command."""
from typer.testing import CliRunner
from flint.cli import app

class TestCalibrateCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        assert result.exit_code == 0
        assert "venue" in result.output.lower()

    def test_dry_run_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        assert "dry-run" in result.output.lower() or "dry_run" in result.output.lower()
