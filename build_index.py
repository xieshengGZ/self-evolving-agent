"""Build a symbol index (.index.json) for all Python files in the project.

Uses only the standard library (ast). Skips .venv/, __pycache__/, .git/ and
backups/ directories.

Usage:
    python build_index.py [--output .index.json]
"""

import argparse
import ast
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, ".index.json")

SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "backups", "state", "node_modules"}


def _module_path(rel_path: str) -> str:
    """app/main.py -> app.main ; app/__init__.py -> app"""
    rel = rel_path.replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [p for p in rel.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            args = ast.unparse(node.args)
        except Exception:  # noqa: BLE001
            args = "..."
        ret = ""
        if node.returns is not None:
            try:
                ret = " -> " + ast.unparse(node.returns)
            except Exception:  # noqa: BLE001
                ret = ""
        return f"({args}){ret}"
    if isinstance(node, ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:  # noqa: BLE001
                pass
        return f"({', '.join(bases)})" if bases else ""
    return ""


def _collect_calls(node: ast.AST) -> List[str]:
    """Collect names of all call targets inside node body (deduped, sorted)."""
    names: List[str] = []
    seen = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name: Optional[str] = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _symbol_entry(node: ast.AST, path: str, kind: str) -> Dict[str, Any]:
    return {
        "path": path,
        "name": getattr(node, "name", ""),
        "kind": kind,
        "signature": _signature(node),
        "line_start": getattr(node, "lineno", 0),
        "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
        "doc": _first_doc_line(node),
        "calls": _collect_calls(node),
    }


def _walk_body(body: List[ast.stmt], prefix: str, out: List[Dict[str, Any]]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_symbol_entry(node, f"{prefix}.{node.name}", "function"))
        elif isinstance(node, ast.ClassDef):
            out.append(_symbol_entry(node, f"{prefix}.{node.name}", "class"))
            _walk_body(node.body, f"{prefix}.{node.name}", out)


def index_file(abs_path: str, rel_path: str) -> Dict[str, Any]:
    """Index a single file. Returns dict with sha256/lines/symbols or error."""
    try:
        with open(abs_path, "rb") as fh:
            raw = fh.read()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"read failed: {exc}"}

    sha = hashlib.sha256(raw).hexdigest()[:12]
    text = raw.decode("utf-8", errors="replace")
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)

    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as exc:
        return {"error": f"SyntaxError: {exc}", "sha256": sha, "lines": lines}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"parse failed: {exc}", "sha256": sha, "lines": lines}

    symbols: List[Dict[str, Any]] = []
    _walk_body(tree.body, _module_path(rel_path), symbols)
    symbols.sort(key=lambda s: (s["line_start"], s["path"]))
    return {"sha256": sha, "lines": lines, "symbols": symbols}


def iter_py_files(root: str = PROJECT_ROOT) -> List[str]:
    """Return sorted relative paths of all .py files under root."""
    result: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            abs_p = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
            result.append(rel)
    return sorted(result)


def build_index(output: str = DEFAULT_OUTPUT, root: str = PROJECT_ROOT) -> Dict[str, Any]:
    """Build the index and write it to output. Returns the index dict."""
    started = time.time()
    files: Dict[str, Any] = {}
    symbol_count = 0
    for rel in iter_py_files(root):
        abs_p = os.path.join(root, rel)
        entry = index_file(abs_p, rel)
        files[rel] = entry
        symbol_count += len(entry.get("symbols", []))

    index = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "project_root": os.path.abspath(root),
        "files": files,
    }
    out_path = output if os.path.isabs(output) else os.path.join(root, output)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    elapsed = time.time() - started
    index["_stats"] = {
        "file_count": len(files),
        "symbol_count": symbol_count,
        "elapsed_sec": round(elapsed, 3),
        "output": out_path,
    }
    return index


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build .index.json symbol index")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output path (default .index.json)")
    args = parser.parse_args(argv)

    index = build_index(args.output)
    stats = index["_stats"]
    print(
        f"扫描 {stats['file_count']} 个文件，{stats['symbol_count']} 个符号，"
        f"耗时 {stats['elapsed_sec']} 秒"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
