# Setup

## Base Conda
- Conda executable: `/Users/lopezama/miniconda3/condabin/conda`
- Base interpreter: `/Users/lopezama/miniconda3/bin/python`

## Project Environment (Python 3.10)
```bash
/Users/lopezama/miniconda3/bin/conda create -y -n sci-cluster-py310 python=3.10
/Users/lopezama/miniconda3/bin/conda activate sci-cluster-py310
```

## Recreate From Spec
```bash
/Users/lopezama/miniconda3/bin/conda env create -f sci-cluster-py310.yml
```

## Interpreter For IDE
- Use: `/Users/lopezama/miniconda3/envs/sci-cluster-py310/bin/python`

## Liam Pressure Runtime (Python 3.10)
- Recommended runtime env: `/Users/lopezama/miniconda3/envs/enki/bin/python`
- Vendored Liam source path: `/Users/lopezama/PycharmProjects/sci-cluster/vendor/LeiTesting`

## Reproducibility Guardrails (MELTS/ThermoEngine)

1. Use Python `3.10.x` (not 3.11+ / 3.12+ / 3.14).
2. Pin NumPy `<2` (recommended `1.26.4`) to avoid `nptyping`/`bool8` breakage.
3. Install `futureproof==0.3.1` and `rubicon-objc>=0.4.2` in the runtime env.
4. Build native ThermoEngine libraries so `equilibrate.MELTSmodel(...)` can instantiate.
5. Ensure `thermoengine` import resolves to the real package, not a namespace shadow.

### Native library build (required for MELTSmodel)
On Apple Silicon/Homebrew:

```bash
cd /Users/lopezama/PycharmProjects/ThermoEngine-master
LIBRARY_PATH=/opt/homebrew/lib make -j4
```

Expected artifact:
- `/Users/lopezama/PycharmProjects/ThermoEngine-master/src/libphaseobjc.dylib`

The Liam runner now auto-loads this local `libphaseobjc.dylib` when bootstrapping from source.

### Common shadowing pitfall
If your workspace contains `ThermoEngine-master/thermoengine` on `sys.path`,
`import thermoengine` can resolve to a namespace package with no `equilibrate`.

The runner now auto-bootstraps from `THERMOENGINE_SRC_ROOT`, but you can set it explicitly:

```bash
export THERMOENGINE_SRC_ROOT=/Users/lopezama/PycharmProjects/ThermoEngine-master/thermoengine
```

Then run:

```bash
python tools/run_liam_pressure_single.py --prepared-liam-csv /path/to/input.csv
```

### Known warnings (currently allowed)
- If `MPLCONFIGDIR` points to a non-writable location, matplotlib may warn on first import.

## ThermoEngine Geobarometer Driver

Single-script workflow (row-wise input CSV):

```bash
python tools/run_thermoengine_geobarometer.py \
  --input-csv /path/to/samples.csv \
  --output-dir outputs/thermoengine-geobarometer-runs \
  --thermoengine-src /Users/lopezama/PycharmProjects/ThermoEngine-master/thermoengine \
  --mode all \
  --pressure-bracket-bar 1500 2500
```

Required columns:
- `sample_id`
- `SiO2, TiO2, Al2O3, Fe2O3, Cr2O3, FeO, MnO, MgO, NiO, CoO, CaO, Na2O, K2O, P2O5, H2O`

Optional columns (with defaults):
- `fO2_offset`, `T_init_K`, `P_init_bar`
- `T0_C`, `Tf_C`, `dT_C`, `P0_MPa`, `Pf_MPa`, `dP_MPa`
- `date`, `notes`, `plumi`

Integration runtime tests are opt-in:

```bash
RUN_THERMOENGINE_INTEGRATION=1 pytest -q tests/test_thermoengine_geobarometer.py
```
