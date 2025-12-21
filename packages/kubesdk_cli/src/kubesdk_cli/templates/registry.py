from __future__ import annotations

from dataclasses import MISSING
from typing import Any, Mapping, Optional

ModelKey = tuple[str, str]  # apiVersion, kind
ALL_RESOURCES: dict[ModelKey, type[Any]] = {}


def maybe_get_model_key(model_class: type[Any]) -> Optional[ModelKey]:
    """Return (apiVersion, kind) if both exist as strings."""

    api_version = getattr(model_class, "apiVersion", None)
    kind = getattr(model_class, "kind", None)
    if isinstance(api_version, str) and isinstance(kind, str):
        return api_version, kind

    dataclass_fields = getattr(model_class, "__dataclass_fields__", None)
    if not isinstance(dataclass_fields, dict):
        return None

    api_version_field = dataclass_fields.get("apiVersion")
    kind_field = dataclass_fields.get("kind")

    def get_string_default(field_object: Any) -> str | None:
        if field_object is None:
            return None

        default_value = getattr(field_object, "default", MISSING)
        if default_value is MISSING:
            default_factory = getattr(field_object, "default_factory", MISSING)
            if default_factory is not MISSING:
                default_value = default_factory()

        return default_value if isinstance(default_value, str) else None

    api_version_default = get_string_default(api_version_field)
    kind_default = get_string_default(kind_field)
    if api_version_default is None or kind_default is None:
        return None

    return api_version_default, kind_default


def register_model(model_class: type[Any]) -> None:
    """Register a model class in the global registry, if it has a resolvable key."""
    model_key = maybe_get_model_key(model_class)
    if model_key is not None:
        ALL_RESOURCES.setdefault(model_key, model_class)


def get_model(api_version: str, kind: str) -> type[Any] | None:
    """Look up a model class by (apiVersion, kind)."""
    return ALL_RESOURCES.get((api_version, kind))


def get_model_by_body(body: Mapping[str, Any]) -> type[Any] | None:
    """Look up a model class by reading apiVersion/kind out of a Kubernetes-style dict."""
    api_version_value = body.get("apiVersion")
    kind_value = body.get("kind")

    if not isinstance(api_version_value, str) or not isinstance(kind_value, str):
        return None

    return get_model(api_version_value, kind_value)
