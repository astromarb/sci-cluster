

set -ex



pip check
tqdm --help
tqdm -v | rg 4.67.1
pytest -k "not tests_perf"
exit 0
