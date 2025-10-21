from typing import TypeVar, Generic
from dataclasses import dataclass, field

# noinspection ALL
from ._k8s_resource_base import K8sResource, loader, ListMeta


ResourceT = TypeVar("ResourceT", bound=K8sResource)


@loader
@dataclass(kw_only=True, frozen=True)
class K8sResourceList(Generic[ResourceT], K8sResource):
    items: list[ResourceT]
    apiVersion: str = "v1"
    metadata: ListMeta = field(default_factory=ListMeta)
