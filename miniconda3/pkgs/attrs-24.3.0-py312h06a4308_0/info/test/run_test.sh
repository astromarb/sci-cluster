

set -ex



pip check
pytest --fixtures tests -vv
exit 0
