# ruff: noqa: E501
"""Input hygiene applied before any adapter runs: text that cannot be stored or serialized is rejected."""

from typing import Any


def _bad(text: str) -> bool:
    # NUL cannot be stored by PostgreSQL; a lone surrogate cannot be encoded as UTF-8 in any response.
    return "\x00" in text or any("\ud800" <= ch <= "\udfff" for ch in text)


def find_invalid_text(value: Any) -> str | None:
    """Path of the first string (or key) holding a NUL or a lone surrogate, else None. Iterative: no recursion limit.

    Only the PATH is returned, never the offending text."""
    stack: list[tuple[Any, str]] = [(value, "$")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, str):
            if _bad(node):
                return path
        elif isinstance(node, dict):
            for k, v in node.items():
                here = f"{path}.{k}" if isinstance(k, str) and not _bad(k) else f"{path}.<key>"
                if isinstance(k, str) and _bad(k):
                    return here
                stack.append((v, here))
        elif isinstance(node, list):
            stack.extend((v, f"{path}[{i}]") for i, v in enumerate(node))
    return None
