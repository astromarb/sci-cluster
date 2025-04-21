

set -ex



pip check
jupyter -h
jupyter-migrate -h
jupyter-troubleshoot --help
pytest -vv -k "not (test_not_on_path or test_path_priority or test_argv0)"
exit 0
