

set -ex



pip check
pytest -vv --pyargs jsonschema_specifications --cov-branch --cov=jsonschema_specifications --cov-report=term-missing:skip-covered --no-cov-on-fail --cov-fail-under=88
exit 0
