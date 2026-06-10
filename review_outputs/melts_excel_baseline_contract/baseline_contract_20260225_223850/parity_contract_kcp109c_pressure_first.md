# KCP-109C Parity Contract (Pressure-Match-First)

## Baseline and target
- Baseline workbook: `/Users/lopezama/Downloads/Parallel_MELTS_KCP-109C_dP25.0_02-25_8cores.xlsx`
- Latest generated parity comparison used: `/Users/lopezama/PycharmProjects/sci-cluster/review_outputs/rmelts_parity_audit_v2/2026-02-26/kcp109c_phase7_parity_20260225_222731/parity_comparison_report.json`
- Immediate parity gate: **2-phase pressure match first**

## Locked correctness constraints (already implemented)
- Composition preserved exactly
- No renormalization
- No implicit Fe repartition
- Pressure threshold semantics match Liam (raw residual minimum before fit)

## Current parity state (pre-fix)
- Liam 2-phase pressure: `249.3518518518341` MPa
- Generated 2-phase pressure: `352.8225806452008` MPa
- Delta (generated - Liam): `103.47072879336667` MPa

## Working diagnosis for next code fix
The current mismatch is likely driven by **runtime path differences in liquidus recovery**, specifically reset/retry behavior after solver-state failure (`matmul` mismatch). The immediate target is to fix liquidus reset/re-seed semantics so post-reset retries remain numerically valid (never `state=None` with `bulk_comp=None`).

## Success definition for next parity cycle
1. Run completes without runaway liquidus temperatures or pinned workers.
2. Generated KCP-109C 2-phase pressure matches Liam (~249.35 MPa) within tolerance.
3. 3-phase threshold behavior remains Liam-compatible at 10°C.
