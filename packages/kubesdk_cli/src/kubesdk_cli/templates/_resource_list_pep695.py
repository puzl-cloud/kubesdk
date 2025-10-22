from typing import List
from dataclasses import dataclass, field

# noinspection ALL
from ._k8s_resource_base import K8sResource, loader, ListMeta


@loader
@dataclass(kw_only=True, frozen=True)
class K8sResourceList[ResourceT: K8sResource](K8sResource):
    items: List[ResourceT]
    apiVersion: str = "v1"
    kind: str = f"{ResourceT.__class__.__name__}List"
    metadata: ListMeta = field(default_factory=ListMeta)
