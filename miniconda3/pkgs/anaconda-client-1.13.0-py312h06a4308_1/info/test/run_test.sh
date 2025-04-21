

set -ex



pip check
export ANACONDA_CLI_FORCE_NEW=1
anaconda -h
anaconda -V
anaconda org --help
python -c "import binstar_client; print(f'binstar_client.__version__ is {binstar_client.__version__}')"
python -c "import binstar_client; assert binstar_client.__version__ == \"1.13.0\""
pytest -v tests
exit 0
