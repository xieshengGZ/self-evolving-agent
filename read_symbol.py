"""Read the source of a single symbol by its dotted path.

Usage:
    python read_symbol.py app.main.agent_loop
    python read_symbol.py app.session.Session.add_message

Importable:
    from read_symbol import get_symbol_source
    code = get_symbol_source("app.main.agent_loop")
"""

import ast
import os
import sys
from typing import List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


class SymbolNotFound(Exception):
    """Raised when a symbol path cannot be resolved."""


class SymbolLookupError(Exception):
    """Raised for other lookup problems (bad path, missing file, syntax error)."""


def _candidate_files(symbol_path: str) -> List[Tuple[str, List[str]]]:
    """Return list of (abs_file, remaining_parts) candidates, longest module first."""
    parts = [p for p in symbol_path.split(".") if p]
    if len(parts) < 2:
        raise SymbolLookupError(
            f"符号路径格式错误: {symbol_path!r}（应形如 app.main.func）"
        )
    candidates: List[Tuple[str, List[str]]] = []
    # module parts are everything except the last 1..N parts (symbol chain)
    for split in range(len(parts) - 1, 0, -1):
        mod_parts = parts[:split]
        rest = parts[split:]
        rel = os.path.join(*mod_parts) + ".py"
        abs_p = os.path.join(PROJECT_ROOT, rel)
        if os.path.isfile(abs_p):
            candidates.append((abs_p, rest))
        # package __init__
        rel_init = os.path.join(*mod_parts, "__init__.py")
        abs_init = os.path.join(PROJECT_ROOT, rel_init)
        if os.path.isfile(abs_init):
            candidates.append((abs_init, rest))
    if not candidates:
        raise SymbolLookupError(f"找不到符号 {symbol_path!r} 对应的文件")
    return candidates


def _find_in_body(body: List[ast.stmt], names: List[str]) -> Optional[ast.AST]:
    """Find nested node matching names chain (e.g. ['Session', 'add_message'])."""
    if not names:
        return None
    target = names[0]
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == target:
            if len(names) == 1:
                return node
            return _find_in_body(node.body, names[1:])
    return None


def _locate(symbol_path: str) -> Tuple[str, ast.AST, List[str]]:
    last_err: Optional[Exception] = None
    for abs_p, rest in _candidate_files(symbol_path):
        try:
            with open(abs_p, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            tree = ast.parse(src, filename=abs_p)
        except SyntaxError as exc:
            last_err = SymbolLookupError(f"解析 {abs_p} 失败: {exc}")
            continue
        except OSError as exc:
            last_err = SymbolLookupError(f"读取 {abs_p} 失败: {exc}")
            continue
        node = _find_in_body(tree.body, rest)
        if node is not None:
            return abs_p, node, src.splitlines()
    if last_err is not None:
        raise last_err
    raise SymbolNotFound(f"symbol not found: {symbol_path}")


def get_symbol_info(symbol_path: str) -> dict:
    """Return dict with file/line_start/line_end/source for a symbol path."""
    abs_p, node, lines = _locate(symbol_path)
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start)
    source = "\n".join(lines[start - 1 : end])
    return {
        "symbol": symbol_path,
        "file": os.path.relpath(abs_p, PROJECT_ROOT).replace(os.sep, "/"),
        "line_start": start,
        "line_end": end,
        "source": source,
    }


def get_symbol_source(symbol_path: str) -> str:
    """Return the source code of the symbol (str). Raises on failure."""
    return get_symbol_info(symbol_path)["source"]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("用法: python read_symbol.py <符号路径，如 app.main.agent_loop>", file=sys.stderr)
        return 1
    try:
        info = get_symbol_info(argv[0])
    except SymbolNotFound as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except SymbolLookupError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(info["source"])
    if not info["source"].endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
