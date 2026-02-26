# Mitigation Decision (Temporary Patch Attempt)

- This run tested a local venv-only compatibility patch at `equilibrate.py:2166` to explicitly extract a scalar from `np.matmul(c_row, mu_elm)`.
- The patch was applied temporarily and the file was restored automatically at the end.
- Numerical validity should be judged by the before/after KCP-109C liquidus probe values in `before_after_validation.json`.
- If values are unchanged and deprecation counts drop, this is a viable documented local workaround (scripted, not committed to site-packages).
