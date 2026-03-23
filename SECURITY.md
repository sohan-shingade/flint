# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it privately via [GitHub Security Advisories](https://github.com/sohan-shingade/flint/security/advisories/new) rather than opening a public issue.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (main) | Yes |

## Security Measures

- **No secrets in code**: API keys are stored in `.env` (gitignored), never in source
- **Strategy sandboxing**: User strategies run in the same process (like Jupyter/Freqtrade) — Flint is a local-first, single-user tool
- **Branch protection**: `main` branch is protected against deletion and force pushes
- **GitHub Actions**: Restricted to GitHub-owned and verified actions only
- **Workflow permissions**: Read-only by default
- **Dependencies**: Regularly updated, no unnecessary dependencies

## Local Security

Flint runs entirely on your machine. No data is sent to external servers except:
- API calls to data providers you explicitly enable (Drift, Birdeye, Helius, etc.)
- RPC calls to Solana if you use live/paper trading

Your `.env` file, strategy code, and trading data never leave your machine.
