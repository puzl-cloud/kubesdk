from __future__ import annotations

from typing import Type

from .loader import Loadable
from .registry import get_model, get_model_by_body
from .resource import K8sResource, K8sResourceList


def get_k8s_resource_model(api_version: str, kind: str) -> Type[K8sResource] | None:
    model = get_model(api_version, kind)
    if model is not None and issubclass(model, K8sResource):
        return model
    return None


__all__ = [
    "get_k8s_resource_model",
    "get_model",
    "get_model_by_body",
    "K8sResource",
    "K8sResourceList",
    "Loadable",
]
