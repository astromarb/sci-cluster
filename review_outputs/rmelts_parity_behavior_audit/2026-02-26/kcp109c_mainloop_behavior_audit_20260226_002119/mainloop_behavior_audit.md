# KCP109C Main-Loop Behavior Audit (Liam baseline vs patched variants)
## Pressure outcomes from runtime tests
- `profile_k`: `P2 = 352.8225806452008 MPa`
- `profile_k_no_timeout_temp_advance`: same `P2`
- `profile_k_no_bound_main_loop_failures`: same `P2`
## Liam vs patched common behavior
- Liam main loop uses timeout wrapper: `False` (expected `False`)
- Liam has bounded fail counter patch markers: `False` (expected `False`)
## Variant-specific toggles tested
### profile_k_vs_profile_k_no_timeout_temp_advance
- `contains_timeout_advance`: base=`True` -> variant=`False`
- `timeout_path_advances_temperature`: base=`True` -> variant=`False`
### profile_k_vs_profile_k_no_bound_main_loop_failures
- `bound_main_loop_failures`: base=`True` -> variant=`False`
- `contains_bounded_fail_counter`: base=`True` -> variant=`False`
- `contains_timeout_abort_message`: base=`True` -> variant=`False`
## Conclusion
- These two shared-factor changes do not move the final pressure or pressure-fit geometry.
- The next parity-breaking layer is likely a deeper common main-loop control-flow difference from Liam, especially timeout-wrapper `None` handling + step/data-append semantics.
