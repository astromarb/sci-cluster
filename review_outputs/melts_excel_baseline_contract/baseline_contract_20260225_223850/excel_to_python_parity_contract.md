# Excel-to-Python Parity Contract (Baseline / Oracle)

## Purpose
This document treats the Excel workbook as a **baseline contract** for parity and structure, not as a reverse-engineering target.

## Baseline contract elements used for parity
- **Bulk composition input**: `BulkComposition` named range (`Input!B2:B16`)
- **State controls**: `Pressure`, `Temperature`, `Status`
- **Thermo output summaries**: `SystemThermo`, `LiquidThermo`, `SolidThermo`
- **Phase summary block**: `PhaseSummary`
- **Pressure collection structure**: `Collect_P` sheet presence and layout

## Python pipeline mapping
- Composition-preserving prep: `MC_to_csv_rMELTS(...)` (no renormalization, no implicit Fe split)
- MELTS batch execution: `rMELTS_run(...)`
- Workbook/tabular extraction: `rMELTS_geobarometry_basis(...)`
- Pressure extraction and fallback: `_read_pressure_analysis_sheet_from_workbook(...)` and `_compute_pressure_analysis_from_workbook(...)`

## Parity constraints (locked)
- Exact composition preservation
- Explicit `FeO` + `Fe2O3` semantics (no implicit `FeOt` repartition)
- Liam-compatible pressure threshold semantics (raw residual min before fit)
- Pressure-match-first gate for KCP-109C

## What is intentionally out of scope in this phase
- VBA macro decompilation/reverse engineering
- UI control replication
- Full workbook logic reconstruction
