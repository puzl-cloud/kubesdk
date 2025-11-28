from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import is_dataclass, MISSING
from typing import Dict, Tuple, Optional, Type, Any

from .loader import LazyLoadModel
from .resource import K8sResource, K8sResourceList


__ALL_RESOURCES: Dict[Tuple[str, str], Type[LazyLoadModel]] = {}

# One-time guard
__INDEX_BUILT: bool = False


def __maybe_get_model_key(cls: Type[Any]) -> Optional[Tuple[str, str]]:
    """
    Return (apiVersion, kind) if both exist as strings.

    Supports:
    - ClassVar[str] / plain class attributes
    - dataclass defaults, including slots=True dataclasses
    """
    # 1. First try plain/classvar attributes
    v = getattr(cls, "apiVersion", None)
    k = getattr(cls, "kind", None)
    if isinstance(v, str) and isinstance(k, str):
        return v, k

    # 2. Fallback: dataclass fields (works for slots / non-slots)
    dc_fields = getattr(cls, "__dataclass_fields__", None)
    if isinstance(dc_fields, dict):
        v_field = dc_fields.get("apiVersion")
        k_field = dc_fields.get("kind")

        def _get_str_default(f):
            if f is None:
                return None
            default = f.default
            if default is MISSING:
                factory = getattr(f, "default_factory", MISSING)
                if factory is not MISSING:
                    default = factory()
            return default if isinstance(default, str) else None

        v_default = _get_str_default(v_field)
        k_default = _get_str_default(k_field)

        if v_default is not None and k_default is not None:
            return v_default, k_default

    return None


def __register_from_module(module) -> None:
    """
    Inspect a module and register any dataclasses that have both apiVersion and kind.
    Only classes defined in the module itself are considered (not re-exports).
    """
    for obj in vars(module).values():
        if inspect.isclass(obj) and is_dataclass(obj) and obj.__module__ == module.__name__:
            model_key = __maybe_get_model_key(obj)
            if model_key:
                obj: Type[K8sResource]
                __ALL_RESOURCES.setdefault(model_key, obj)


def __discover_all_submodules() -> None:
    """Import all submodules of this package."""
    if "__path__" not in globals():
        # Not a package
        return
    prefix = __name__ + "."
    for _finder, modname, _ispkg in pkgutil.walk_packages(__path__, prefix=prefix):
        # Skip private files
        if modname.split(".")[-1].startswith("_"):
            continue

        mod = importlib.import_module(modname)
        __register_from_module(mod)


def __build_index() -> None:
    """
    Build the DATACLASSES_BY_GVK index once.
    """
    global __INDEX_BUILT
    if __INDEX_BUILT:
        return
    __discover_all_submodules()
    __INDEX_BUILT = True


__build_index()


def get_model(api_version: str, kind: str) -> Type[LazyLoadModel]:
    return __ALL_RESOURCES.get((api_version, kind))


def get_k8s_resource_model(api_version: str, kind: str) -> Type[K8sResource] | None:
    model = get_model(api_version, kind)
    if model and issubclass(model, K8sResource):
        return model
    return None


def get_model_by_body(body: Dict) -> Type[LazyLoadModel]:
    api_version, kind = body.get("apiVersion"), body.get("kind")
    return get_model(api_version, kind)


__all__ = ["get_k8s_resource_model", "get_model", "get_model_by_body", "K8sResource", "K8sResourceList"]
