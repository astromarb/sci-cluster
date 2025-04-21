

set -ex



pip check
jupyter trust --version
jupyter-trust --help
pip install pep440
pytest -vv
exit 0
