#!/usr/bin/env python3
"""Codemap generator: one markdown shard per package into docs/codemap/.

Walks the ``flint`` package with the stdlib ``ast`` module (no imports of the
target code, so it runs even when dependencies are missing) and emits, for each
package directory, a compact markdown shard listing:

  * each module's first docstring line,
  * every top-level class/function signature + first docstring line,
    (classes include their method signatures, indented),
  * the internal import edges (which sibling ``flint.*`` modules it pulls in).

Each shard header carries the source git hash so a reader knows which tree the
map describes. Output is fully deterministic (sorted), so regenerating on an
unchanged tree is a no-op — the map is idempotent and diff-friendly.

Usage:  python scripts/codemap.py            # map ./flint into docs/codemap/
        python scripts/codemap.py --check    # exit 1 if the map is stale
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "flint"
OUTPUT_DIR = REPO_ROOT / "docs" / "codemap"
TOP_PACKAGE = "flint"


def git_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _fmt_args(args: ast.arguments) -> str:
    parts: list[str] = []
    posonly = getattr(args, "posonlyargs", [])
    all_pos = posonly + args.args
    defaults = list(args.defaults)
    # right-align defaults onto the tail of the positional args
    pad = len(all_pos) - len(defaults)
    for i, a in enumerate(all_pos):
        s = a.arg
        if a.annotation is not None:
            s += f": {ast.unparse(a.annotation)}"
        if i >= pad:
            d = defaults[i - pad]
            s += f"={ast.unparse(d)}"
        parts.append(s)
        if posonly and a is posonly[-1]:
            parts.append("/")
    if args.vararg is not None:
        s = "*" + args.vararg.arg
        if args.vararg.annotation is not None:
            s += f": {ast.unparse(args.vararg.annotation)}"
        parts.append(s)
    elif args.kwonlyargs:
        parts.append("*")
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        s = a.arg
        if a.annotation is not None:
            s += f": {ast.unparse(a.annotation)}"
        if d is not None:
            s += f"={ast.unparse(d)}"
        parts.append(s)
    if args.kwarg is not None:
        s = "**" + args.kwarg.arg
        if args.kwarg.annotation is not None:
            s += f": {ast.unparse(args.kwarg.annotation)}"
        parts.append(s)
    return ", ".join(parts)


def _sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({_fmt_args(node.args)}){ret}"


def _first_doc_line(node) -> str:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _is_public(name: str) -> bool:
    return not name.startswith("_") or name in ("__init__",)


def _internal_imports(tree: ast.Module) -> list[str]:
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == TOP_PACKAGE:
                    edges.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import — record as a relative edge
                edges.add("." * node.level + (node.module or ""))
            elif node.module and node.module.split(".")[0] == TOP_PACKAGE:
                edges.add(node.module)
    return sorted(edges)


def _render_module(path: Path, module_name: str) -> list[str]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:  # keep the map generating even if one file is broken
        return [f"### `{module_name}`", f"- (unparseable: {exc})", ""]

    lines: list[str] = [f"### `{module_name}`"]
    mod_doc = _first_doc_line(tree)
    if mod_doc:
        lines.append(f"_{mod_doc}_")

    edges = _internal_imports(tree)
    if edges:
        lines.append(f"- imports: {', '.join(f'`{e}`' for e in edges)}")

    body_items = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            body_items.append(node)
        elif isinstance(node, ast.ClassDef) and _is_public(node.name):
            body_items.append(node)

    for node in body_items:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            head = f"class {node.name}" + (f"({bases})" if bases else "")
            doc = _first_doc_line(node)
            lines.append(f"- `{head}`" + (f" — {doc}" if doc else ""))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(sub.name):
                    sdoc = _first_doc_line(sub)
                    lines.append(f"    - `{_sig(sub)}`" + (f" — {sdoc}" if sdoc else ""))
        else:
            doc = _first_doc_line(node)
            lines.append(f"- `{_sig(node)}`" + (f" — {doc}" if doc else ""))

    lines.append("")
    return lines


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _package_key(path: Path) -> str:
    """Shard key = the package (directory) a module lives in, dotted."""
    rel = path.parent.relative_to(REPO_ROOT)
    return ".".join(rel.parts)


def build_shards() -> dict[str, str]:
    """Return {shard_filename: markdown_content} for the current tree."""
    if not PACKAGE_ROOT.exists():
        return {}

    py_files = sorted(
        p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )
    by_package: dict[str, list[Path]] = {}
    for p in py_files:
        by_package.setdefault(_package_key(p), []).append(p)

    ghash = git_hash()
    shards: dict[str, str] = {}
    for pkg, files in sorted(by_package.items()):
        lines = [
            f"# Codemap: `{pkg}`",
            "",
            f"> source git hash: `{ghash}`",
            "> generated by `scripts/codemap.py` — do not edit by hand",
            "",
        ]
        for f in sorted(files):
            lines.extend(_render_module(f, _module_name(f)))
        shards[f"{pkg}.md"] = "\n".join(lines).rstrip() + "\n"
    return shards


def write_shards(shards: dict[str, str]) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # remove stale shards no longer backed by a package
    wanted = set(shards)
    for existing in OUTPUT_DIR.glob("*.md"):
        if existing.name not in wanted:
            existing.unlink()
    for name, content in sorted(shards.items()):
        dest = OUTPUT_DIR / name
        if not dest.exists() or dest.read_text(encoding="utf-8") != content:
            dest.write_text(content, encoding="utf-8")
        written.append(dest)
    return written


def _without_hash_line(content: str) -> str:
    """Drop the volatile `> source git hash:` header line for staleness comparison.

    The hash line changes on every commit, so comparing it would report the
    committed codemap as stale the moment HEAD moves past the regen commit.
    """
    return "\n".join(
        line for line in content.splitlines() if not line.startswith("> source git hash:")
    )


def check_stale(shards: dict[str, str]) -> bool:
    """Return True if the on-disk map differs from freshly generated shards.

    The comparison ignores the source-git-hash header line so a committed,
    up-to-date codemap does not read as stale simply because HEAD advanced.
    """
    if not OUTPUT_DIR.exists():
        return bool(shards)
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in OUTPUT_DIR.glob("*.md")}
    if on_disk.keys() != shards.keys():
        return True
    return any(
        _without_hash_line(on_disk[name]) != _without_hash_line(content)
        for name, content in shards.items()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed codemap is stale (do not write)",
    )
    args = parser.parse_args(argv)

    shards = build_shards()
    if args.check:
        if check_stale(shards):
            print("codemap is STALE — run `python scripts/codemap.py`", file=sys.stderr)
            return 1
        print(f"codemap up to date ({len(shards)} shards)")
        return 0

    if not shards:
        print(f"no python packages found under {PACKAGE_ROOT} — nothing to map")
        return 0
    written = write_shards(shards)
    print(f"wrote {len(written)} codemap shard(s) to {OUTPUT_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
