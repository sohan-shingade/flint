// The user-source validation report (§13.2, D25). A "invalid" verdict is a
// *completed* run whose source failed the static screen or the OS sandbox — data an
// author revises against, rendered first-class: line-precise screen violations, the
// structured sandbox error (type + message, never a stack trace), and the look-ahead
// findings. The AST/import screen is lint-grade UX; the sandbox is the real boundary.

import type { ValidationReport } from '../api/types'

export function ValidationPanel({ report }: { report: ValidationReport }) {
  const { screen_violations: violations, sandbox_error: sandbox, lookahead } = report

  return (
    <div className="space-y-4" data-testid="validation-panel">
      <div className="flex items-center gap-2">
        <span className="rounded bg-loss/20 px-2 py-0.5 font-mono text-xs font-semibold uppercase tracking-wide text-loss">
          invalid · {report.stage}
        </span>
        <span className="font-mono text-xs text-ghost">
          the strategy did not pass validation — nothing was run or persisted
        </span>
      </div>

      {/* Static screen — line-precise import/AST violations. */}
      {violations.length > 0 && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">screen violations</h3>
          <ul className="space-y-1 font-mono text-sm">
            {violations.map((v, i) => (
              <li key={i} className="flex gap-3">
                <span className="shrink-0 text-ghost">
                  L{v.line}:{v.col}
                </span>
                <span className="shrink-0 text-loss">{v.code}</span>
                <span className="text-terminal">{v.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Sandbox runtime failure — contained, structured, no stack trace. */}
      {sandbox && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">sandbox error</h3>
          <div className="rounded border border-loss/40 bg-loss/5 p-3 font-mono text-sm">
            <span className="text-loss">{sandbox.type}</span>
            <span className="text-terminal"> — {sandbox.message}</span>
          </div>
        </section>
      )}

      {/* Look-ahead lint — warnings, honest about what it did and didn't check. */}
      {(lookahead.findings.length > 0 || lookahead.summary) && (
        <section>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-amber">look-ahead lint</h3>
          {lookahead.summary && <div className="mb-2 font-mono text-sm text-ghost">{lookahead.summary}</div>}
          {lookahead.findings.length > 0 && (
            <ul className="space-y-1 font-mono text-sm">
              {lookahead.findings.map((f, i) => (
                <li key={i} className="flex gap-3">
                  {f.line !== null && <span className="shrink-0 text-ghost">L{f.line}</span>}
                  <span className="shrink-0 text-amber">{f.category}</span>
                  <span className="text-terminal">{f.message}</span>
                </li>
              ))}
            </ul>
          )}
          {lookahead.blind_spots.length > 0 && (
            <div className="mt-2 font-mono text-xs text-ghost">
              blind spots: {lookahead.blind_spots.join(', ')}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
