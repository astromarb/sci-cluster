# Mitigation Decision (Subprocess Patch Test)

- Patched three scalar-assignment sites (`2166`, `3669`, `3717`) with explicit scalar extraction.
- Patch applied only to local venv for test and restored automatically.
- Use `summary.json` and `before_after_validation.json` to decide whether this is a safe documented workaround.
