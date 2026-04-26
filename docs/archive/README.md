# Archive

Historical artifacts kept for context but no longer load-bearing on
current development. Active planning lives at the repo root
(`ROADMAP.md`, `WAVE_STATUS.md`, `DEFERRED.md`, `CHANGELOG.md`).

## Contents

| File | What it is | Superseded by |
|---|---|---|
| `BUG_REPORT_2026-04-24.md` | 4 smoke-run bugs found pre-v1.4.0; fixes regression-tested | `tests/test_smoke_regressions.py` |
| `v1.4.0-pr.md` | PR body for the big restructure release (41 commits, +8000/-2400 LOC) | [v1.4.0 release notes](https://github.com/sohan-shingade/flint/releases/tag/v1.4.0) |
| `IMPLEMENTATION_PLAN.md` | Master plan derived from the 2026-04-23 audit | `WAVE_STATUS.md` (delivery log) + `DEFERRED.md` (open items) + `ROADMAP.md` (index) |
| `mev-guide.md` | MEV bot patterns (sandwich, sniping) | Out of wedge per Apr 2026 product review — Flint is a perp-strategy lab, not a MEV scanner |
| `mev-infrastructure-analysis.md` | MEV infra notes | Same — out of wedge |

Keep these files committed so historical commit links resolve, but
don't treat them as authoritative.
