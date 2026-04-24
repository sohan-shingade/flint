#!/usr/bin/env python3
"""Build UI docs from markdown source files.

Converts docs/guides/*.md and docs/tutorials/*.md into
ui/src/data/docs-content.ts so the web UI serves documentation
from the same source of truth as the markdown files.

Run: python scripts/build_docs.py
Auto-runs during: npm run build (via package.json prebuild hook)
"""
import html
import re
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUT = PROJECT_ROOT / "ui" / "src" / "data" / "docs-content.ts"

# Sections define the order and grouping in the UI sidebar
SECTIONS = [
    {
        "id": "getting-started",
        "title": "GETTING STARTED",
        "files": [
            ("docs/guides/quickstart.md", "Quickstart"),
            ("docs/README.md", "Documentation Index"),
        ],
    },
    {
        "id": "tutorials",
        "title": "TUTORIALS",
        "files": [
            ("docs/tutorials/01-install-first-backtest.md", "1. Install + First Backtest"),
            ("docs/tutorials/02-author-a-strategy.md", "2. Author a Strategy"),
            ("docs/tutorials/03-optimize-walk-forward.md", "3. Optimize + Walk-Forward"),
            ("docs/tutorials/04-paper-to-live.md", "4. Paper to Live"),
            ("docs/tutorials/05-cross-venue-funding-arb.md", "5. Cross-Venue Funding Arb"),
            ("docs/tutorials/06-custom-data-provider.md", "6. Custom Data Provider"),
        ],
    },
    {
        "id": "how-to",
        "title": "HOW-TO",
        "files": [
            ("docs/how-to/download-data.md", "Download Market Data"),
            ("docs/how-to/calibrate-slippage.md", "Calibrate Slippage"),
            ("docs/how-to/run-parity-test.md", "Run a Parity Test"),
            ("docs/how-to/add-api-keys.md", "Add API Keys"),
            ("docs/how-to/run-rust-engine.md", "Enable the Rust Engine"),
            ("docs/how-to/compare-backtests.md", "Compare Backtests"),
            ("docs/how-to/configure-risk-guards.md", "Configure Risk Guards"),
            ("docs/how-to/resume-paper-session.md", "Resume a Paper Session"),
        ],
    },
    {
        "id": "reference",
        "title": "REFERENCE",
        "files": [
            ("docs/reference/rest-api.md", "REST API"),
            ("docs/reference/cli.md", "CLI"),
            ("docs/reference/python-sdk.md", "Python SDK"),
            ("docs/reference/mcp-tools.md", "MCP Tools"),
            ("docs/reference/config.md", "Config"),
            ("docs/reference/strategy-templates.md", "Strategy Templates"),
            ("docs/reference/data-providers.md", "Data Providers"),
            ("docs/reference/indicators.md", "Indicators"),
            ("docs/reference/venue-configs.md", "Venue Configs"),
            ("docs/reference/metrics.md", "Metrics"),
        ],
    },
    {
        "id": "concepts",
        "title": "CONCEPTS",
        "files": [
            ("docs/concepts/architecture.md", "Architecture"),
            ("docs/concepts/execution-contexts.md", "Execution Contexts"),
            ("docs/concepts/fill-pipeline.md", "Fill Pipeline"),
            ("docs/concepts/margin-capital.md", "Margin & Capital"),
            ("docs/concepts/regimes.md", "Market Regimes"),
            ("docs/concepts/risk-model.md", "Risk Model"),
            ("docs/concepts/backtests-vs-reality.md", "Backtests vs Reality"),
        ],
    },
    {
        "id": "validation",
        "title": "VALIDATION & SAFETY",
        "files": [
            ("docs/validation/safety-rails.md", "Safety Rails"),
            ("docs/validation/known-limitations.md", "Known Limitations"),
            ("docs/validation/devnet-testing-guide.md", "Devnet Testing"),
            ("docs/validation/fill-model-comparison.md", "Fill Model Comparison"),
        ],
    },
    {
        "id": "ui-guide",
        "title": "WEB UI",
        "files": [
            ("docs/guides/web-ui.md", "Web UI Guide"),
        ],
    },
]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(
        c.replace("-", "").replace(":", "").strip() == "" for c in cells
    )


def _split_row(line: str) -> list[str]:
    """Split a pipe-table row into cells. Handles optional leading/trailing pipes."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def md_to_html(md: str) -> tuple[str, list[dict]]:
    """Convert markdown to simple HTML + extract code blocks.

    State machine for block-level: code fences, tables, lists, headings, paragraphs.
    Inline (bold/code/italic/links) handled by _inline.

    Returns (html_content, code_blocks) where code_blocks is a list
    of {"language": str, "code": str} dicts.
    """
    code_blocks: list[dict] = []
    lines = md.split("\n")
    out: list[str] = []

    in_code = False
    code_lang = ""
    code_buf: list[str] = []

    in_table = False
    table_header_seen = False  # have we emitted the <tr><th>...</th></tr>?
    pending_header: list[str] | None = None  # cells from a pending header row

    in_list: str | None = None  # "ul" / "ol" / None

    def close_list():
        nonlocal in_list
        if in_list is not None:
            out.append(f"</{in_list}>")
            in_list = None

    def close_table():
        nonlocal in_table, table_header_seen, pending_header
        if in_table:
            out.append("</table>")
        in_table = False
        table_header_seen = False
        pending_header = None

    for raw in lines:
        line = raw.rstrip()

        # --- fenced code blocks ---
        if line.startswith("```") and not in_code:
            close_list()
            close_table()
            in_code = True
            code_lang = line[3:].strip() or "text"
            code_buf = []
            continue
        if line.startswith("```") and in_code:
            in_code = False
            code_blocks.append({"language": code_lang, "code": "\n".join(code_buf)})
            continue
        if in_code:
            code_buf.append(raw)
            continue

        # --- skip first H1 (title shown by UI header) ---
        if line.startswith("# ") and not out and not in_table and in_list is None:
            continue

        stripped = line.strip()

        # --- tables (pipe rows) ---
        is_pipe_row = stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2
        # Or a header row without leading/trailing pipes (less common): skip that case.

        if is_pipe_row:
            close_list()
            cells = _split_row(stripped)

            if _is_separator_row(cells):
                # separator after header → commit pending header as <th>
                if pending_header is not None and not table_header_seen:
                    if not in_table:
                        out.append("<table class='docs-table'>")
                        in_table = True
                    out.append(
                        "<tr>" + "".join(
                            f"<th>{_inline(c)}</th>" for c in pending_header
                        ) + "</tr>"
                    )
                    table_header_seen = True
                    pending_header = None
                continue

            # data or header row
            if not in_table and pending_header is None:
                # first pipe row — hold it; it becomes header iff separator follows
                pending_header = cells
                continue

            # already inside table, or we had a pending header and this is another content row
            if pending_header is not None and not in_table:
                # header row with no separator following — treat as no-header table; flush as body
                out.append("<table class='docs-table'>")
                in_table = True
                out.append(
                    "<tr>" + "".join(
                        f"<td>{_inline(c)}</td>" for c in pending_header
                    ) + "</tr>"
                )
                pending_header = None

            out.append(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>"
            )
            continue

        # non-pipe line — close any open table
        if in_table or pending_header is not None:
            # pending header never got a separator → flush as body row
            if pending_header is not None and not in_table:
                out.append("<table class='docs-table'>")
                in_table = True
                out.append(
                    "<tr>" + "".join(
                        f"<td>{_inline(c)}</td>" for c in pending_header
                    ) + "</tr>"
                )
                pending_header = None
            close_table()

        # --- headings ---
        if line.startswith("#### "):
            close_list()
            out.append(f"<h4>{_inline(line[5:])}</h4>")
            continue
        if line.startswith("### "):
            close_list()
            out.append(f"<h4>{_inline(line[4:])}</h4>")
            continue
        if line.startswith("## "):
            close_list()
            out.append(f"<h4>{_inline(line[3:])}</h4>")
            continue
        if line.startswith("# "):
            close_list()
            out.append(f"<h4>{_inline(line[2:])}</h4>")
            continue

        # --- blockquote ---
        if line.startswith("> "):
            close_list()
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
            continue

        # --- horizontal rule ---
        if stripped in ("---", "***", "___"):
            close_list()
            out.append("<hr />")
            continue

        # --- lists ---
        if re.match(r"^[-*] ", line):
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if m:
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # --- blank line ---
        if stripped == "":
            close_list()
            continue

        # --- paragraph ---
        close_list()
        out.append(f"<p>{_inline(line)}</p>")

    # end-of-input cleanup
    close_list()
    close_table()

    return "\n".join(out), code_blocks


def _inline(text: str) -> str:
    """Convert inline markdown (bold, code, links) to HTML."""
    # Code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def build():
    topics_by_section = {}

    for section in SECTIONS:
        topics = []
        for filepath, title in section["files"]:
            full_path = PROJECT_ROOT / filepath
            if not full_path.exists():
                print(f"  SKIP {filepath} (not found)")
                continue

            md = full_path.read_text()
            content_html, code_blocks = md_to_html(md)

            topic_id = Path(filepath).stem
            topics.append({
                "id": topic_id,
                "title": title,
                "content": content_html,
                "codeBlocks": code_blocks,
            })
            print(f"  OK {filepath} -> {topic_id} ({len(content_html)} chars, {len(code_blocks)} code blocks)")

        topics_by_section[section["id"]] = {
            "id": section["id"],
            "title": section["title"],
            "topics": topics,
        }

    # Generate TypeScript
    ts_lines = [
        "// AUTO-GENERATED from docs/guides/*.md and docs/tutorials/*.md",
        "// Run: python scripts/build_docs.py",
        "// Do not edit this file directly — edit the markdown sources instead.",
        "",
        "export interface DocCodeBlock {",
        "  language: string",
        "  code: string",
        "}",
        "",
        "export interface DocTopic {",
        "  id: string",
        "  title: string",
        "  content: string",
        "  codeBlocks?: DocCodeBlock[]",
        "}",
        "",
        "export interface DocSection {",
        "  id: string",
        "  title: string",
        "  topics: DocTopic[]",
        "}",
        "",
        "export const docsContent: DocSection[] = ",
    ]

    import json
    sections_list = [topics_by_section[s["id"]] for s in SECTIONS if s["id"] in topics_by_section]
    ts_lines.append(json.dumps(sections_list, indent=2, ensure_ascii=False))

    OUTPUT.write_text("\n".join(ts_lines) + "\n")
    print(f"\nWrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    print("Building docs-content.ts from markdown sources...")
    build()
