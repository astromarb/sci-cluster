

set -ex



cd tests && pytest -vv -m "not benchmark"
pip check
exit 0
