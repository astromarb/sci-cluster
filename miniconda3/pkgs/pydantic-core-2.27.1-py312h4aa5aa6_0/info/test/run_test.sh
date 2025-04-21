

set -ex



pip check
python -c "from pydantic_core import PydanticUndefinedType"
exit 0
