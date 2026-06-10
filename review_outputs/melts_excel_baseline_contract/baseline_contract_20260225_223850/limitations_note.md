# Limitations (Baseline Contract Extraction Phase)

This phase intentionally extracts only workbook **I/O contract and parity-relevant structural clues**.

Not performed in this phase:
- VBA decompilation / macro reverse engineering
- exhaustive formula documentation
- Excel UI behavior replication

Rationale:
- The goal is to use Excel as a **baseline/oracle** for a Python-native implementation, not to re-implement the workbook.
