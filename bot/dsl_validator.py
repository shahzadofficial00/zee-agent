import json
import logging
from pathlib import Path
import jsonschema
from jsonschema import validate, ValidationError

logger = logging.getLogger(__name__)

# Load schema once at startup
_SCHEMA_PATH = Path(__file__).parent.parent / "dsl-spec" / "schemas" / "v1" / "schema.json"

def _load_schema() -> dict:
    try:
        with open(_SCHEMA_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"❌ DSL schema not found at {_SCHEMA_PATH}")
        return {}

_SCHEMA = _load_schema()


def validate_dsl(dsl: dict) -> tuple[bool, str]:
    """
    Validate a DSL payload against schema.json.
    Returns (is_valid, error_message).
    """
    if not _SCHEMA:
        logger.warning("⚠️ Schema not loaded — skipping validation")
        return True, ""

    try:
        validate(instance=dsl, schema=_SCHEMA)
        return True, ""
    except ValidationError as e:
        return False, e.message


def safe_send_dsl(dsl: dict) -> dict | None:
    """
    Validate DSL before sending. Returns DSL if valid, None if invalid.
    Use this instead of sending raw DSL directly.
    """
    is_valid, error = validate_dsl(dsl)
    if not is_valid:
        logger.error(f"❌ Invalid DSL blocked | type={dsl.get('type')} | error={error}")
        return None
    logger.info(f"✅ DSL valid | type={dsl.get('type')}")
    return dsl