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
            ("docs/tutorials/first-strategy.md", "Tutorial: First Strategy"),
        ],
    },
    {
        "id": "guides",
        "title": "GUIDES",
        "files": [
            ("docs/guides/web-ui.md", "Web UI"),
            ("docs/guides/strategy-authoring.md", "Strategy Authoring"),
            ("docs/guides/data-providers.md", "Data Providers"),
            ("docs/guides/live-deployment.md", "Live Deployment"),
            ("docs/guides/mcp-integration.md", "MCP Integration"),
            ("docs/guides/slippage-models.md", "Slippage Models"),
        ],
    },
    {
        "id": "reference",
        "title": "REFERENCE",
        "files": [
            ("docs/guides/architecture.md", "Architecture"),
            ("docs/validation/safety-rails.md", "Safety Rails"),
            ("docs/validation/devnet-testing-guide.md", "Devnet Testing"),
        ],
    },
]


def md_to_html(md: str) -> tuple[str, list[dict]]:
    """Convert markdown to simple HTML + extract code blocks.

    Returns (html_content, code_blocks) where code_blocks is a list
    of {"language": str, "code": str} dicts.
    """
    code_blocks = []
    lines = md.split("\n")
    out = []
    in_code = False
    code_lang = ""
    code_buf = []

    for line in lines:
        if line.startswith("```") and not in_code:
            in_code = True
            code_lang = line[3:].strip() or "text"
            code_buf = []
            continue
        elif line.startswith("```") and in_code:
            in_code = False
            code_blocks.append({"language": code_lang, "code": "\n".join(code_buf)})
            continue
        elif in_code:
            code_buf.append(line)
            continue

        # Skip the title (first H1)
        if line.startswith("# ") and not out:
            continue

        # Headers
        if line.startswith("#### "):
            out.append(f"<h4>{html.escape(line[5:])}</h4>")
        elif line.startswith("### "):
            out.append(f"<h4>{html.escape(line[4:])}</h4>")
        elif line.startswith("## "):
            out.append(f"<h4>{html.escape(line[3:])}</h4>")
        # Table handling
        elif line.startswith("|"):
            if not out or not out[-1].startswith("<table"):
                out.append("<table class='docs-table'>")
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                continue  # skip separator row
            tag = "th" if not any("<tr>" in o for o in out[-5:] if o.startswith("<tr>")) else "td"
            # After first row, use td
            if out[-1] != "<table class='docs-table'>":
                tag = "td"
            row = "<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>"
            out.append(row)
        else:
            # Close table if we were in one
            if out and (out[-1].startswith("<tr>") or out[-1].startswith("<table")):
                if not line.startswith("|"):
                    out.append("</table>")

            # List items
            if line.startswith("- "):
                if not out or not out[-1].endswith("</li>"):
                    out.append("<ul>")
                out.append(f"<li>{_inline(line[2:])}</li>")
            elif re.match(r"^\d+\. ", line):
                content = re.sub(r"^\d+\. ", "", line)
                if not out or not out[-1].endswith("</li>"):
                    out.append("<ol>")
                out.append(f"<li>{_inline(content)}</li>")
            elif line.strip() == "":
                # Close lists
                if out and out[-1].endswith("</li>"):
                    # Find if we're in ul or ol
                    for prev in reversed(out):
                        if prev.startswith("<ul>"):
                            out.append("</ul>")
                            break
                        elif prev.startswith("<ol>"):
                            out.append("</ol>")
                            break
                        elif prev in ("</ul>", "</ol>"):
                            break
                out.append("")
            else:
                out.append(f"<p>{_inline(line)}</p>")

    # Close any open tags
    if out and out[-1].endswith("</li>"):
        for prev in reversed(out):
            if prev.startswith("<ul>"):
                out.append("</ul>")
                break
            elif prev.startswith("<ol>"):
                out.append("</ol>")
                break
            elif prev in ("</ul>", "</ol>"):
                break
    if out and (out[-1].startswith("<tr>") or out[-1].startswith("<table")):
        out.append("</table>")

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
