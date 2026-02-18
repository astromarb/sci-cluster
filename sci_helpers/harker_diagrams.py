"""Utilities for generating Harker diagrams from pandas DataFrames."""

from __future__ import annotations

import math
import warnings
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DEFAULT_Y_OXIDES = [
    "TiO2",
    "Al2O3",
    "FeOt",
    "MnO",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
    "P2O5",
]

DEFAULT_OXIDE_PALETTE = {
    "TiO2": "#0072B2",  # blue
    "Al2O3": "#E69F00",  # orange
    "FeOt": "#D55E00",  # vermillion
    "MnO": "#CC79A7",  # reddish purple
    "MgO": "#009E73",  # bluish green
    "CaO": "#56B4E9",  # sky blue
    "Na2O": "#332288",  # indigo
    "K2O": "#000000",  # black
    "P2O5": "#999999",  # gray
}


def _warn_if_enabled(message: str, warn: bool) -> None:
    if warn:
        warnings.warn(message, stacklevel=2)


def _validate_trendline(trendline: str | None) -> str | None:
    allowed = {None, "linear", "theil_sen", "both"}
    if trendline not in allowed:
        raise ValueError(
            f"Invalid trendline '{trendline}'. Must be one of: None, 'linear', 'theil_sen', 'both'."
        )
    return trendline


def _resolve_y_oxides(df: pd.DataFrame, y_oxides: list[str] | None) -> list[str]:
    if y_oxides is not None:
        return y_oxides
    return [oxide for oxide in DEFAULT_Y_OXIDES if oxide in df.columns]


def _normalize_oxide_rows(plot_df: pd.DataFrame, oxide_cols: list[str], warn: bool) -> pd.DataFrame:
    """Normalize selected oxide columns to 100 wt.% on each row."""
    if not oxide_cols:
        return plot_df
    sums = plot_df[oxide_cols].sum(axis=1, min_count=1)
    valid = sums > 0
    invalid_count = int((~valid).sum())
    if invalid_count:
        _warn_if_enabled(
            f"normalize_oxides=True: {invalid_count} rows have non-positive oxide totals and were left unnormalized.",
            warn,
        )
    plot_df.loc[valid, oxide_cols] = plot_df.loc[valid, oxide_cols].div(sums.loc[valid], axis=0) * 100.0
    return plot_df


def _plot_linear_trend(ax: Any, x: np.ndarray, y: np.ndarray) -> None:
    if len(x) < 2:
        return
    coeffs = np.polyfit(x, y, 1)
    x_line = np.linspace(np.nanmin(x), np.nanmax(x), 100)
    y_line = coeffs[0] * x_line + coeffs[1]
    ax.plot(x_line, y_line, linestyle="--", linewidth=1.2, color="black", alpha=0.8, label="Linear fit")


def _plot_theilsen_trend(ax: Any, x: np.ndarray, y: np.ndarray, warn: bool) -> None:
    if len(x) < 2:
        return
    try:
        from sklearn.linear_model import TheilSenRegressor
    except Exception:
        _warn_if_enabled(
            "scikit-learn is not available. Skipping Theil-Sen trendline.",
            warn,
        )
        return

    model = TheilSenRegressor(random_state=0)
    model.fit(x.reshape(-1, 1), y)
    x_line = np.linspace(np.nanmin(x), np.nanmax(x), 100)
    y_line = model.predict(x_line.reshape(-1, 1))
    ax.plot(
        x_line,
        y_line,
        linestyle="-.",
        linewidth=1.2,
        color="dimgray",
        alpha=0.9,
        label="Theil-Sen fit",
    )


def plot_harker_diagrams(
    df: pd.DataFrame,
    x_oxide: str = "SiO2",
    y_oxides: list[str] | None = None,
    sample_id_col: str | None = None,
    group_col: str | None = None,
    color: str = "#1f77b4",
    palette: dict[str, str] | None = None,
    label_points: bool = False,
    error_suffix: str = "_err",
    use_error_bars: bool = True,
    normalize_oxides: bool = False,
    trendline: str | None = None,
    figsize: tuple[float, float] = (14, 10),
    marker_size: float = 18,
    alpha: float = 0.8,
    save_path: str | None = None,
    dpi: int = 300,
    warn: bool = True,
) -> tuple[plt.Figure, dict[str, plt.Axes], pd.DataFrame]:
    """Plot a grid of Harker diagrams (x_oxide vs many y_oxides).

    Returns:
        tuple: (figure, axes_by_oxide, cleaned_df_union)
    """
    if x_oxide not in df.columns:
        raise ValueError(f"Missing required x_oxide column: '{x_oxide}'")

    trendline = _validate_trendline(trendline)
    resolved_y_oxides = _resolve_y_oxides(df, y_oxides)
    if not resolved_y_oxides:
        raise ValueError(
            "No valid y_oxides found. Pass y_oxides explicitly or provide default major oxide columns."
        )

    missing_y = [oxide for oxide in resolved_y_oxides if oxide not in df.columns]
    if missing_y:
        raise ValueError(f"Missing required y_oxide columns: {missing_y}")

    use_grouping = False
    if group_col is not None and group_col in df.columns and df[group_col].notna().any():
        use_grouping = True
    elif group_col is not None:
        _warn_if_enabled(
            f"group_col '{group_col}' missing or empty. Falling back to single-color plotting.",
            warn,
        )

    plot_df = df.copy()
    numeric_cols = [x_oxide] + resolved_y_oxides
    for c in numeric_cols:
        plot_df[c] = pd.to_numeric(plot_df[c], errors="coerce")

    if normalize_oxides:
        norm_cols = [c for c in [x_oxide] + resolved_y_oxides if c in plot_df.columns]
        plot_df = _normalize_oxide_rows(plot_df, norm_cols, warn=warn)

    if use_error_bars:
        for oxide in [x_oxide] + resolved_y_oxides:
            err_col = f"{oxide}{error_suffix}"
            if err_col in plot_df.columns:
                plot_df[err_col] = pd.to_numeric(plot_df[err_col], errors="coerce")

    n_panels = len(resolved_y_oxides)
    n_cols = min(3, n_panels)
    n_rows = int(math.ceil(n_panels / n_cols))

    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)
    axes_arr = np.atleast_1d(axes).ravel()
    axes_by_oxide: dict[str, plt.Axes] = {}

    used_indices: set[Any] = set()

    for i, y_oxide in enumerate(resolved_y_oxides):
        ax = axes_arr[i]
        axes_by_oxide[y_oxide] = ax
        panel_color = (palette or {}).get(y_oxide, DEFAULT_OXIDE_PALETTE.get(y_oxide, color))

        panel_df = plot_df[[x_oxide, y_oxide] + ([group_col] if use_grouping else [])].copy()
        valid_mask = panel_df[x_oxide].notna() & panel_df[y_oxide].notna()
        panel_df = panel_df.loc[valid_mask]

        if panel_df.empty:
            _warn_if_enabled(f"No valid rows for panel {x_oxide} vs {y_oxide}. Skipping.", warn)
            ax.set_visible(False)
            continue

        used_indices.update(panel_df.index.tolist())

        xerr_col = f"{x_oxide}{error_suffix}"
        yerr_col = f"{y_oxide}{error_suffix}"
        xerr = None
        yerr = None

        if use_error_bars and xerr_col in plot_df.columns:
            xerr_series = pd.to_numeric(plot_df.loc[panel_df.index, xerr_col], errors="coerce")
            if xerr_series.notna().any():
                xerr = xerr_series.to_numpy()
            else:
                _warn_if_enabled(
                    f"Error column '{xerr_col}' is present but has no valid numeric values for {y_oxide}.",
                    warn,
                )

        if use_error_bars and yerr_col in plot_df.columns:
            yerr_series = pd.to_numeric(plot_df.loc[panel_df.index, yerr_col], errors="coerce")
            if yerr_series.notna().any():
                yerr = yerr_series.to_numpy()
            else:
                _warn_if_enabled(
                    f"Error column '{yerr_col}' is present but has no valid numeric values.",
                    warn,
                )

        if use_grouping:
            groups = panel_df[group_col].fillna("Unknown").astype(str)
            unique_groups = list(dict.fromkeys(groups.tolist()))
            for group_name in unique_groups:
                group_mask = groups == group_name
                xvals = panel_df.loc[group_mask, x_oxide].to_numpy()
                yvals = panel_df.loc[group_mask, y_oxide].to_numpy()
                group_color = (palette or {}).get(group_name, color)

                if xerr is not None or yerr is not None:
                    gxerr = xerr[group_mask.to_numpy()] if xerr is not None else None
                    gyerr = yerr[group_mask.to_numpy()] if yerr is not None else None
                    ax.errorbar(
                        xvals,
                        yvals,
                        xerr=gxerr,
                        yerr=gyerr,
                        fmt="o",
                    color=group_color,
                        ecolor=group_color,
                        markersize=max(2.0, marker_size ** 0.5),
                        alpha=alpha,
                        capsize=1.5,
                        linestyle="none",
                        label=group_name,
                    )
                else:
                    ax.scatter(xvals, yvals, s=marker_size, color=group_color, alpha=alpha, label=group_name)
        else:
            xvals = panel_df[x_oxide].to_numpy()
            yvals = panel_df[y_oxide].to_numpy()
            if xerr is not None or yerr is not None:
                ax.errorbar(
                    xvals,
                    yvals,
                    xerr=xerr,
                    yerr=yerr,
                    fmt="o",
                    color=panel_color,
                    ecolor=panel_color,
                    markersize=max(2.0, marker_size ** 0.5),
                    alpha=alpha,
                    capsize=1.5,
                    linestyle="none",
                )
            else:
                ax.scatter(xvals, yvals, s=marker_size, color=panel_color, alpha=alpha)

        if label_points:
            if sample_id_col is None or sample_id_col not in df.columns:
                _warn_if_enabled(
                    "label_points=True but sample_id_col missing/invalid. Skipping annotations.",
                    warn,
                )
            else:
                for idx in panel_df.index:
                    label = str(df.at[idx, sample_id_col])
                    ax.annotate(
                        label,
                        (panel_df.at[idx, x_oxide], panel_df.at[idx, y_oxide]),
                        fontsize=8,
                        alpha=0.6,
                    )

        if trendline is not None:
            xvals = panel_df[x_oxide].to_numpy(dtype=float)
            yvals = panel_df[y_oxide].to_numpy(dtype=float)
            if len(xvals) < 2:
                _warn_if_enabled(
                    f"Insufficient points for trendline in panel {x_oxide} vs {y_oxide}.",
                    warn,
                )
            else:
                if trendline in {"linear", "both"}:
                    _plot_linear_trend(ax, xvals, yvals)
                if trendline in {"theil_sen", "both"}:
                    _plot_theilsen_trend(ax, xvals, yvals, warn=warn)

        ax.set_xlabel(f"{x_oxide} wt.%")
        ax.set_ylabel(f"{y_oxide} wt.%")
        ax.set_title(y_oxide, fontsize=11, fontweight="bold", pad=6)
        ax.grid(alpha=0.2)
        if use_grouping:
            ax.legend(frameon=False, fontsize=8)

        yticks = ax.get_yticks()
        if len(yticks) >= 4:
            ax.set_yticks(yticks[::2])

    for j in range(n_panels, len(axes_arr)):
        axes_arr[j].set_visible(False)

    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")

    if used_indices:
        cleaned_df_union = df.loc[sorted(used_indices)].copy()
    else:
        cleaned_df_union = df.iloc[0:0].copy()

    return fig, axes_by_oxide, cleaned_df_union


def plot_harker_diagrams_from_compositions(
    compositions: list[dict[str, Any]] | list[list[Any]],
    columns: list[str] | None = None,
    **kwargs: Any,
) -> tuple[plt.Figure, dict[str, plt.Axes], pd.DataFrame]:
    """Wrapper for Monte Carlo composition lists.

    Accepts:
    - list[dict]: each dict is one synthetic sample with oxide keys
    - list[list] + columns: tabular rows with explicit column names
    """
    if not compositions:
        raise ValueError("compositions is empty.")

    first = compositions[0]
    if isinstance(first, dict):
        df = pd.DataFrame(compositions)
    else:
        if columns is None:
            raise ValueError("columns must be provided for list-of-lists compositions.")
        df = pd.DataFrame(compositions, columns=columns)

    return plot_harker_diagrams(df=df, **kwargs)
