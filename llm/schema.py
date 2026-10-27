"""A small JSON Schema subset, enough to revalidate what Claude returns (no jsonschema dependency).

Supported: type (one name, required at every level), enum, properties, required, additionalProperties
(boolean), items (one schema), minItems, maxItems, minLength, maxLength, minimum, maximum; description and title
are annotations. Any other keyword is refused by
check_schema(): a bound the validator would silently skip would look enforced and not be.

Error messages name the path in the schema, never a value from the output (it may carry CV content).
"""
import math

TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
KEYWORDS = {"type", "enum", "properties", "required", "additionalProperties", "items",
            "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"}
ANNOTATIONS = {"description", "title", "$schema"}


class SchemaDefinitionError(ValueError):
    """The schema itself uses something this validator does not enforce. A programming error."""


class SchemaValidationError(ValueError):
    """The instance does not match the schema."""


def check_schema(schema, path="$"):
    if not isinstance(schema, dict):
        raise SchemaDefinitionError(f"{path}: a schema must be an object")
    unknown = set(schema) - KEYWORDS - ANNOTATIONS
    if unknown:
        raise SchemaDefinitionError(f"{path}: unsupported keywords {sorted(unknown)}")
    # Every level names one type: the API's structured outputs need it, and the SDK's transform_schema()
    # does not take a list of types.
    if not isinstance(schema.get("type"), str) or schema["type"] not in TYPES:
        raise SchemaDefinitionError(f"{path}: missing or unsupported type")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise SchemaDefinitionError(f"{path}: enum must be a list")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise SchemaDefinitionError(f"{path}: properties must be an object")
        for name, sub in schema["properties"].items():
            check_schema(sub, f"{path}.{name}")
    if "required" in schema and not (isinstance(schema["required"], list)
                                     and all(isinstance(name, str) for name in schema["required"])):
        raise SchemaDefinitionError(f"{path}: required must be a list of names")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaDefinitionError(f"{path}: additionalProperties must be true or false")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and not (_is_int(schema[key]) and schema[key] >= 0):
            raise SchemaDefinitionError(f"{path}: {key} must be a non-negative integer")
    for key in ("minimum", "maximum"):
        if key in schema and not _is_number(schema[key]):
            raise SchemaDefinitionError(f"{path}: {key} must be a number")


def validate(instance, schema, path="$"):
    """Raise SchemaValidationError unless `instance` (parsed JSON) matches `schema`."""
    if "type" in schema and not _has_type(instance, schema["type"]):
        raise SchemaValidationError(f"{path}: wrong type")
    if "enum" in schema and not any(_same(instance, choice) for choice in schema["enum"]):
        raise SchemaValidationError(f"{path}: value not allowed")
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                raise SchemaValidationError(f"{path}: missing {name}")
        if schema.get("additionalProperties", True) is False and set(instance) - set(properties):
            # The extra key itself comes from the output: not repeated here.
            raise SchemaValidationError(f"{path}: unexpected property")
        for name, value in instance.items():
            if name in properties:
                validate(value, properties[name], f"{path}.{name}")
    elif isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: too few items")
        if "items" in schema:
            for index, value in enumerate(instance):
                validate(value, schema["items"], f"{path}[{index}]")
    elif isinstance(instance, str):
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: too long")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: too short")
    elif _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: above maximum")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return (_is_int(value) or isinstance(value, float)) and math.isfinite(value)


def _has_type(value, name):
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: _is_int(value),
        "number": lambda: _is_number(value),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }[name]()


def _same(a, b):
    # In Python True == 1: JSON does not mix them.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b
