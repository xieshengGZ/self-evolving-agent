"""Print the symbol signatures of a Python file (no implementations).

Usage:
    python read_file_summary.py app/main.py

Output format:
    app.main.agent_loop          L125-L210  () -> None
"""

import ast
import os
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _module_path(rel_path: str) -> str:
    rel = rel_path.replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [p for p in rel.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


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


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _walk(body: List[ast.stmt], prefix: str, out: List[Dict[str, Any]]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(
                {
                    "path": f"{prefix}.{node.name}",
                    "signature": _signature(node),
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "doc": _first_doc_line(node),
                }
            )
        elif isinstance(node, ast.ClassDef):
            out.append(
                {
                    "path": f"{prefix}.{node.name}",
                    "signature": _signature(node),
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "doc": _first_doc_line(node),
                }
            )
            _walk(node.body, f"{prefix}.{node.name}", out)


def summarize_file(path: str) -> List[Dict[str, Any]]:
    """Return list of symbol dicts for the given file path (relative to project root)."""
    abs_p = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(abs_p):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(abs_p, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    tree = ast.parse(src, filename=abs_p)
    rel = os.path.relpath(abs_p, PROJECT_ROOT).replace(os.sep, "/")
    symbols: List[Dict[str, Any]] = []
    _walk(tree.body, _module_path(rel), symbols)
    symbols.sort(key=lambda s: (s["line_start"], s["path"]))
    return symbols


def format_summary(symbols: List[Dict[str, Any]]) -> str:
    if not symbols:
        return ""
    width = max(len(s["path"]) for s in symbols)
    lines = []
    for s in symbols:
        rng = f"L{s['line_start']}-L{s['line_end']}"
        lines.append(f"{s['path'].ljust(width)}  {rng.ljust(12)}  {s['signature']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("用法: python read_file_summary.py <文件路径，如 app/main.py>", file=sys.stderr)
        return 1
    try:
        symbols = summarize_file(argv[0])
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except SyntaxError as exc:
        print(f"错误: 语法错误 {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    out = format_summary(symbols)
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
