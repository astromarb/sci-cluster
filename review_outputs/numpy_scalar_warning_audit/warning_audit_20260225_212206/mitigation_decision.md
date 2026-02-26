# Mitigation Decision (Phase B, provisional)

- Continue diagnosis; do not patch site-packages permanently yet.
- Evidence so far suggests the deprecation warning site is likely a separate compatibility issue from the liquidus `A @ P_nz` matmul failure.
- Next compatibility step (after parity): create a scripted local patch/workaround (not direct committed site-packages edits) if shapes remain scalar-like and numerical results are unchanged.
