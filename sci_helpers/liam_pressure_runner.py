"""Run Liam's pressure calculator on one prepared composition at a time."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import ctypes
import importlib
import importlib.metadata
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import pandas as pd

from .liam_input_converter import (
    DEFAULT_REQUIRED_DATASET_OXIDES,
    LIAM_OXIDE_ROWS,
    REQUIRED_MELTS_PARAMS,
    build_and_write_liam_input_for_sample,
)

VALID_FO2_CONSTRAINT_VALUES = {"TRUE", "FALSE", "T", "F", "1", "0", "YES", "NO", "Y", "N"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_vendor_dir() -> Path:
    return _repo_root() / "vendor" / "LeiTesting"


def _default_run_root() -> Path:
    return _repo_root() / "outputs" / "liam-pressure-runs"


def _safe_version(distribution_name: str) -> str | None:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _purge_thermoengine_modules() -> None:
    for module_name in [name for name in sys.modules if name == "thermoengine" or name.startswith("thermoengine.")]:
        sys.modules.pop(module_name, None)


def _is_valid_thermoengine_src_root(path: Path) -> bool:
    return (path / "thermoengine" / "__init__.py").exists()


def _candidate_native_lib_dirs(thermoengine_src_root: Path) -> list[Path]:
    candidates = [thermoengine_src_root / "src", thermoengine_src_root.parent / "src"]
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _load_local_thermoengine_native_libs(thermoengine_src_root: Path) -> str | None:
    for lib_dir in _candidate_native_lib_dirs(thermoengine_src_root):
        libphaseobjc = lib_dir / ("libphaseobjc.dylib" if sys.platform == "darwin" else "libphaseobjc.so")
        if not libphaseobjc.exists():
            continue
        try:
            ctypes.cdll.LoadLibrary(str(libphaseobjc))
            return str(libphaseobjc)
        except OSError as exc:
            raise RuntimeError(
                f"Found thermoengine native library at {libphaseobjc}, but failed to load it: {exc}"
            ) from exc
    return None


def _candidate_thermoengine_src_roots() -> list[Path]:
    candidates: list[Path] = []

    env_root = os.environ.get("THERMOENGINE_SRC_ROOT")
    if env_root:
        env_path = Path(env_root).expanduser().resolve()
        candidates.extend([env_path, env_path / "thermoengine"])

    sibling_root = _repo_root().parent / "ThermoEngine-master"
    candidates.extend([sibling_root, sibling_root / "thermoengine"])

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _attempt_thermoengine_import() -> dict[str, Any]:
    import thermoengine  # noqa: PLC0415
    from thermoengine import equilibrate  # noqa: PLC0415

    if not hasattr(equilibrate, "MELTSmodel"):
        raise ImportError("thermoengine.equilibrate is importable but missing MELTSmodel.")
    try:
        equilibrate.MELTSmodel("1.0.2")
    except Exception as exc:
        raise ImportError(
            "thermoengine.equilibrate.MELTSmodel exists but failed to initialize. "
            "Ensure libphaseobjc is built and loadable."
        ) from exc

    module_file = getattr(thermoengine, "__file__", None)
    module_paths = [str(p) for p in getattr(thermoengine, "__path__", [])]

    return {
        "module_file": module_file,
        "module_paths": module_paths,
    }


def _ensure_thermoengine_importable() -> dict[str, Any]:
    for candidate in _candidate_thermoengine_src_roots():
        if not _is_valid_thermoengine_src_root(candidate):
            continue
        native_lib = _load_local_thermoengine_native_libs(candidate)
        candidate_text = str(candidate)
        if candidate_text in sys.path:
            sys.path.remove(candidate_text)
        sys.path.insert(0, candidate_text)
        _purge_thermoengine_modules()
        importlib.invalidate_caches()
        try:
            info = _attempt_thermoengine_import()
            info["bootstrap_path"] = candidate_text
            info["native_lib_path"] = native_lib
            return info
        except Exception:
            continue

    try:
        _purge_thermoengine_modules()
        importlib.invalidate_caches()
        info = _attempt_thermoengine_import()
        info["bootstrap_path"] = None
        info["native_lib_path"] = None
        return info
    except Exception as last_error:
        raise RuntimeError(
            "Unable to import thermoengine.equilibrate with MELTSmodel. "
            "Set THERMOENGINE_SRC_ROOT to your ThermoEngine source root "
            "(the directory that contains `thermoengine/__init__.py`) "
            "or install thermoengine in the active Python 3.10 environment."
        ) from last_error


def preflight_liam_runtime(strict: bool = False) -> dict[str, Any]:
    """
    Check runtime compatibility before importing Liam's modules.

    Set strict=True to raise RuntimeError when required dependencies are missing.
    """
    report: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "checks": {},
        "issues": [],
        "warnings": [],
    }

    if sys.version_info[:2] != (3, 10):
        report["issues"].append(
            "Liam/ThermoEngine calculations are validated on Python 3.10. "
            f"Current interpreter is {report['python_version']}."
        )

    numpy_version: str | None = None
    try:
        import numpy as np  # noqa: PLC0415

        numpy_version = np.__version__
        report["checks"]["numpy"] = numpy_version
        major = int(np.__version__.split(".")[0])
        if major >= 2:
            report["issues"].append(
                "NumPy >=2 is not supported by the current thermoengine+nptyping stack. "
                "Pin NumPy to 1.26.4."
            )
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["checks"]["numpy"] = f"ERROR: {exc}"
        report["issues"].append(f"Failed to import numpy: {exc}")

    try:
        import nptyping  # noqa: PLC0415

        report["checks"]["nptyping"] = getattr(nptyping, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        text = str(exc)
        report["checks"]["nptyping"] = f"ERROR: {text}"
        if "bool8" in text and numpy_version:
            report["issues"].append(
                "nptyping import failed due NumPy bool8 API mismatch. "
                "Use numpy==1.26.4."
            )
        else:
            report["issues"].append(f"Failed to import nptyping: {text}")

    try:
        import scipy  # noqa: PLC0415

        report["checks"]["scipy"] = getattr(scipy, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["checks"]["scipy"] = f"ERROR: {exc}"
        report["issues"].append(f"Failed to import scipy: {exc}")

    try:
        import openpyxl  # noqa: PLC0415

        report["checks"]["openpyxl"] = getattr(openpyxl, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["checks"]["openpyxl"] = f"ERROR: {exc}"
        report["issues"].append(f"Failed to import openpyxl: {exc}")

    try:
        from PIL import Image as _PILImage  # noqa: PLC0415, F401

        report["checks"]["pillow"] = _safe_version("pillow") or "unknown"
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["checks"]["pillow"] = f"ERROR: {exc}"
        report["issues"].append(f"Failed to import pillow: {exc}")

    futureproof_version = _safe_version("futureproof")
    if futureproof_version is None:
        report["checks"]["futureproof"] = "missing"
        report["warnings"].append(
            "futureproof is missing. MeltsFP will use stdlib executor fallback. "
            "Install `futureproof==0.3.1` if you want Liam's original threading backend."
        )
    else:
        report["checks"]["futureproof"] = futureproof_version

    thermoengine_version = _safe_version("thermoengine")
    report["checks"]["thermoengine"] = thermoengine_version or "metadata-missing"
    try:
        thermo_info = _ensure_thermoengine_importable()
        report["checks"]["thermoengine_path"] = (
            thermo_info.get("module_file")
            or ", ".join(thermo_info.get("module_paths", []))
            or "unknown"
        )
        report["checks"]["thermoengine_native_lib"] = thermo_info.get("native_lib_path") or "not-set"
        if thermo_info.get("bootstrap_path"):
            report["warnings"].append(
                "thermoengine import was bootstrapped from source path "
                f"{thermo_info['bootstrap_path']}."
            )
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["checks"]["thermoengine_path"] = f"ERROR: {exc}"
        report["issues"].append(str(exc))

    if thermoengine_version is None and not str(report["checks"].get("thermoengine_path", "")).startswith("ERROR:"):
        report["warnings"].append(
            "thermoengine package metadata is missing, but import succeeded. "
            "This usually means a source-path bootstrap or editable install."
        )

    if sys.platform == "darwin":
        rubicon_version = _safe_version("rubicon-objc")
        report["checks"]["rubicon-objc"] = rubicon_version or "missing"
        if rubicon_version is None:
            report["issues"].append(
                "rubicon-objc is missing. ThermoEngine MELTS wrappers require it on macOS."
            )
    else:
        report["checks"]["rubicon-objc"] = _safe_version("rubicon-objc") or "not-required-on-this-platform"

    try:
        import _distutils_hack  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - dependency/runtime check
        report["warnings"].append(
            "Optional warning: _distutils_hack missing. "
            "This can produce noisy startup warnings in this env. "
            f"Current error: {exc}"
        )

    if strict and report["issues"]:
        issues_text = "\n".join(f"- {item}" for item in report["issues"])
        raise RuntimeError(f"Liam runtime preflight failed:\n{issues_text}")

    return report


def _load_liam_module(vendor_code_dir: Path, module_name: str = "MeltsFP"):
    _ensure_thermoengine_importable()

    vendor_resolved = Path(vendor_code_dir).expanduser().resolve()
    if not vendor_resolved.exists():
        raise FileNotFoundError(f"Liam vendor directory does not exist: {vendor_resolved}")

    vendor_path_text = str(vendor_resolved)
    if vendor_path_text not in sys.path:
        sys.path.insert(0, vendor_path_text)

    try:
        module = importlib.import_module(module_name)
        module = importlib.reload(module)
    except Exception as exc:  # pragma: no cover - import surface check
        hint = (
            f"Failed importing {module_name} from {vendor_resolved}. "
            "Verify active env has thermoengine + futureproof and NumPy<2."
        )
        raise ImportError(hint) from exc

    if not hasattr(module, "parallel_melts_main_loop"):
        raise AttributeError(
            f"{module_name} is missing parallel_melts_main_loop. "
            f"Source used: {vendor_resolved}"
        )
    return module


def validate_prepared_liam_csv(prepared_csv: str | Path) -> pd.DataFrame:
    prepared_path = Path(prepared_csv).expanduser().resolve()
    if not prepared_path.exists():
        raise FileNotFoundError(f"Prepared Liam CSV not found: {prepared_path}")

    table = pd.read_csv(prepared_path, index_col=0)
    if table.empty:
        raise ValueError(f"Prepared Liam CSV is empty: {prepared_path}")
    if table.shape[1] < 1:
        raise ValueError(f"Prepared Liam CSV has no composition columns: {prepared_path}")

    required_rows = list(LIAM_OXIDE_ROWS[:15]) + list(REQUIRED_MELTS_PARAMS)
    missing_rows = [row for row in required_rows if row not in table.index]
    if missing_rows:
        missing_text = ", ".join(missing_rows)
        raise ValueError(
            f"Prepared Liam CSV is missing required rows: {missing_text}. "
            f"File: {prepared_path}"
        )

    for col in table.columns:
        for oxide in LIAM_OXIDE_ROWS[:15]:
            numeric = pd.to_numeric(table.at[oxide, col], errors="coerce")
            if pd.isna(numeric):
                raise ValueError(
                    f"Prepared Liam CSV contains blank/non-numeric '{oxide}' "
                    f"for composition column '{col}'."
                )

        calc_type = str(table.at["Calculation", col]).strip()
        if calc_type != "QF_P_Calc":
            raise ValueError(
                f"Only 'QF_P_Calc' is currently supported. "
                f"Found '{calc_type}' for composition '{col}'."
            )

        model = str(table.at["Model", col]).strip()
        if not model:
            raise ValueError(f"Model is blank for composition '{col}'.")

        fo2_constraint = str(table.at["fO2 constraint", col]).strip().upper()
        if fo2_constraint not in VALID_FO2_CONSTRAINT_VALUES:
            raise ValueError(
                f"Invalid fO2 constraint '{table.at['fO2 constraint', col]}' "
                f"for composition '{col}'. Use TRUE/FALSE."
            )

        if "T unit" in table.index:
            t_unit = str(table.at["T unit", col]).strip().upper().replace("°", "")
            if t_unit not in {"C", "CELSIUS"}:
                raise ValueError(f"Invalid T unit '{table.at['T unit', col]}' for composition '{col}'.")
        if "P unit" in table.index:
            p_unit = str(table.at["P unit", col]).strip().upper()
            if p_unit != "MPA":
                raise ValueError(f"Invalid P unit '{table.at['P unit', col]}' for composition '{col}'.")

    return table


class _ChangeDirectory:
    def __init__(self, target_dir: Path):
        self._target = target_dir
        self._original = Path.cwd()

    def __enter__(self):
        os.chdir(self._target)
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        os.chdir(self._original)


@dataclass
class SingleLiamRunConfig:
    wrangled_csv: Path
    sample_id: str
    melts_params: Mapping[str, Any]
    fixed_oxide_overrides: Mapping[str, float] = field(default_factory=dict)
    required_dataset_oxides: tuple[str, ...] | list[str] = DEFAULT_REQUIRED_DATASET_OXIDES
    max_composition_workers: int = 1
    max_pressure_workers: int = 1
    vendor_code_dir: Path = field(default_factory=_default_vendor_dir)
    run_root_dir: Path = field(default_factory=_default_run_root)
    verbose: bool = False


@dataclass
class PreparedInputRunConfig:
    prepared_liam_csv: Path
    max_composition_workers: int = 1
    max_pressure_workers: int = 1
    vendor_code_dir: Path = field(default_factory=_default_vendor_dir)
    run_root_dir: Path = field(default_factory=_default_run_root)
    verbose: bool = False


def _normalize_result_paths(results: list[dict[str, Any]], run_dir: Path) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in results:
        item = dict(row)
        filename = item.get("filename")
        if isinstance(filename, str) and filename:
            path = Path(filename)
            if not path.is_absolute():
                path = (run_dir / path).resolve()
            item["filename"] = str(path)
        normalized.append(item)
    return normalized


def run_single_liam_pressure_from_prepared_csv(config: PreparedInputRunConfig) -> dict[str, Any]:
    preflight = preflight_liam_runtime(strict=True)

    prepared_liam_csv = Path(config.prepared_liam_csv).expanduser().resolve()
    prepared_table = validate_prepared_liam_csv(prepared_liam_csv)

    if config.max_composition_workers != 1:
        raise ValueError("For single-sample runs, use max_composition_workers=1.")
    if prepared_table.shape[1] != 1:
        raise ValueError(
            "Prepared Liam CSV must contain exactly one composition column for single-sample runs. "
            f"Found {prepared_table.shape[1]} columns."
        )

    sample_column = str(prepared_table.columns[0])

    run_root = Path(config.run_root_dir).expanduser().resolve()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"{run_id}_{sample_column}"
    run_dir.mkdir(parents=True, exist_ok=True)
    mpl_config_dir = run_dir / ".matplotlib"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

    liam_module = _load_liam_module(config.vendor_code_dir, module_name="MeltsFP")

    with _ChangeDirectory(run_dir):
        results = liam_module.parallel_melts_main_loop(
            str(prepared_liam_csv),
            max_composition_workers=config.max_composition_workers,
            max_pressure_workers=config.max_pressure_workers,
            verbose=config.verbose,
        )

    normalized_results = _normalize_result_paths(results, run_dir)
    successful = [row for row in normalized_results if "error" not in row]
    failed = [row for row in normalized_results if "error" in row]
    workbook_paths = [
        row["filename"] for row in successful if isinstance(row.get("filename"), str)
    ]

    return {
        "sample_id": sample_column,
        "prepared_liam_csv": str(prepared_liam_csv),
        "run_dir": str(run_dir),
        "max_pressure_workers": config.max_pressure_workers,
        "max_composition_workers": config.max_composition_workers,
        "successful_count": len(successful),
        "failed_count": len(failed),
        "workbooks": workbook_paths,
        "results": normalized_results,
        "preflight": preflight,
    }


def run_single_liam_pressure_from_wrangled(config: SingleLiamRunConfig) -> dict[str, Any]:
    run_root = Path(config.run_root_dir).expanduser().resolve()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    input_dir = run_root / f"{run_id}_{config.sample_id}" / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    liam_input_name = (
        f"liam_input_{Path(config.wrangled_csv).stem}__{config.sample_id}.csv"
    )
    liam_input_csv = input_dir / liam_input_name

    build_and_write_liam_input_for_sample(
        wrangled_csv=config.wrangled_csv,
        sample_id=config.sample_id,
        melts_params=config.melts_params,
        output_csv=liam_input_csv,
        fixed_oxide_overrides=config.fixed_oxide_overrides,
        required_dataset_oxides=config.required_dataset_oxides,
    )

    prepared_cfg = PreparedInputRunConfig(
        prepared_liam_csv=liam_input_csv,
        max_composition_workers=config.max_composition_workers,
        max_pressure_workers=config.max_pressure_workers,
        vendor_code_dir=config.vendor_code_dir,
        run_root_dir=config.run_root_dir,
        verbose=config.verbose,
    )
    summary = run_single_liam_pressure_from_prepared_csv(prepared_cfg)
    summary["wrangled_csv"] = str(Path(config.wrangled_csv).expanduser().resolve())
    return summary
