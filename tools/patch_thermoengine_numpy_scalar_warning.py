#!/usr/bin/env python3
"""
Patch thermoengine scalar-coercion deprecation warning sites (NumPy >=1.25).

This is a local environment compatibility patch for the installed thermoengine
package in the current Python interpreter. It is intentionally *not* applied
automatically by the pipeline.

What it patches:
    - explicit scalar extraction at known scalar-assignment sites where
      thermoengine currently assigns a NumPy array to a scalar slot and triggers:
      "Conversion of an array with ndim > 0 to a scalar is deprecated..."

Usage:
    python tools/patch_thermoengine_numpy_scalar_warning.py --check
    python tools/patch_thermoengine_numpy_scalar_warning.py --apply
    python tools/patch_thermoengine_numpy_scalar_warning.py --restore

Notes:
    - Backs up the target file once as `<file>.numpy_scalar_patch.bak`
    - Restore restores from that backup if present
    - This script only patches the local interpreter's site-packages file
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


PATCH_NAME = "numpy_scalar_patch_v1"


def _thermoengine_equilibrate_path() -> Path:
    import thermoengine.equilibrate as eq  # type: ignore

    return Path(eq.__file__).resolve()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup_path(target: Path) -> Path:
    return target.with_name(target.name + ".numpy_scalar_patch.bak")


def _replacement_text() -> list[tuple[str, str]]:
    # Scalar extraction is explicit and shape-agnostic for scalar-like arrays.
    repl = "float(__import__('numpy').asarray(np.matmul(c_row, mu_elm)).reshape(-1)[0]) if valid else 0.0"
    return [
        ("mu_end[i] = np.matmul(c_row, mu_elm) if valid else 0.0", f"mu_end[i] = {repl}"),
        ("dmu_dc[i,j] = np.matmul(c_row, dmu_dc[:,j]) if valid else 0.0", "dmu_dc[i,j] = float(__import__('numpy').asarray(np.matmul(c_row, dmu_dc[:,j])).reshape(-1)[0]) if valid else 0.0"),
        ("d2mu_dc2[i,j,k] = np.matmul(c_row, d2mu_dc2[:,j,k]) if valid else 0.0", "d2mu_dc2[i,j,k] = float(__import__('numpy').asarray(np.matmul(c_row, d2mu_dc2[:,j,k])).reshape(-1)[0]) if valid else 0.0"),
    ]


def _analyze_text(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for old, new in _replacement_text():
        counts[f"old::{old[:40]}"] = text.count(old)
        counts[f"new::{new[:40]}"] = text.count(new)
    return counts


def _apply_patch_text(text: str) -> tuple[str, dict[str, int]]:
    replacements = 0
    patched = text
    for old, new in _replacement_text():
        if old in patched:
            patched = patched.replace(old, new)
            replacements += 1
    return patched, {"replacements_applied": replacements}


def cmd_check(target: Path) -> int:
    text = target.read_text(encoding="utf-8", errors="ignore")
    backup = _backup_path(target)
    print(f"target={target}")
    print(f"sha256={_sha256(target)}")
    print(f"backup_exists={backup.exists()}")
    print(f"patch_name={PATCH_NAME}")
    for k, v in _analyze_text(text).items():
        print(f"{k}={v}")
    return 0


def cmd_apply(target: Path) -> int:
    text = target.read_text(encoding="utf-8", errors="ignore")
    backup = _backup_path(target)
    patched, meta = _apply_patch_text(text)
    if meta["replacements_applied"] == 0:
        print("No matching thermoengine scalar-coercion lines found. Nothing applied.")
        return 0
    if not backup.exists():
        shutil.copy2(target, backup)
        print(f"Backup written: {backup}")
    target.write_text(patched, encoding="utf-8")
    print(f"Applied {meta['replacements_applied']} replacement(s) to {target}")
    print(f"new_sha256={_sha256(target)}")
    return 0


def cmd_restore(target: Path) -> int:
    backup = _backup_path(target)
    if not backup.exists():
        print(f"No backup found: {backup}")
        return 1
    shutil.copy2(backup, target)
    print(f"Restored {target} from {backup}")
    print(f"restored_sha256={_sha256(target)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore", action="store_true")
    args = parser.parse_args(argv)

    try:
        target = _thermoengine_equilibrate_path()
    except Exception as exc:
        print(f"Could not import thermoengine.equilibrate: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return cmd_check(target)
    if args.apply:
        return cmd_apply(target)
    if args.restore:
        return cmd_restore(target)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
