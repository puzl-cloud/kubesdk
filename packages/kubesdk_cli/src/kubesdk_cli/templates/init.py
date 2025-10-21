from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import is_dataclass
from typing import Dict, Tuple, Optional, Type

from .resource import K8sResource, K8sResourceList


__ALL_RESOURCES: Dict[Tuple[str, str], Type[K8sResource]] = {}

# One-time guard
__INDEX_BUILT: bool = False


def __maybe_get_model_key(cls) -> Optional[Tuple[str, str]]:
    """Return (apiVersion, kind) if both exist as strings (supports ClassVar[str] or defaults)."""
    v = getattr(cls, "apiVersion", None)
    k = getattr(cls, "kind", None)
    if isinstance(v, str) and isinstance(k, str):
        return v, k
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


def get_k8s_resource_model(api_version: str, kind: str) -> Type[K8sResource]:
    return __ALL_RESOURCES.get((api_version, kind))


def get_k8s_resource_model_by_body(body: Dict) -> Type[K8sResource]:
    api_version, kind = body.get("apiVersion"), body.get("kind")
    return get_k8s_resource_model(api_version, kind)


__all__ = ["get_k8s_resource_model", "get_k8s_resource_model_by_body", "K8sResource", "K8sResourceList"]
