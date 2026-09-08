"""Compile a rendered XLSForm with pyxform — the authoritative syntax check."""

from __future__ import annotations

from pathlib import Path


def compile_check(xlsx_path: Path) -> list[str]:
    """Return [] when pyxform accepts the workbook, else its error lines."""
    try:
        from pyxform.xls2xform import convert
    except Exception as exc:  # pragma: no cover - environment problem
        return [f"pyxform is unavailable: {exc}"]

    try:
        convert(str(xlsx_path), warnings=[])
    except Exception as exc:
        text = str(exc).strip() or exc.__class__.__name__
        return [line.strip() for line in text.splitlines() if line.strip()]
    return []
