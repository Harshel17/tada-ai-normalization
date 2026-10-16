"""Imports result_json from scripts/snapshot_canonical.py so tests and the script share one implementation."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "scripts" / "snapshot_canonical.py"
_spec = importlib.util.spec_from_file_location("snapshot_canonical", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
result_json = _mod.result_json


def load_script(name: str):
    """Import any file from scripts/ as a module (scripts are not a package)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod
