from typing import ClassVar, Optional, Set, List
from functools import cached_property
from dataclasses import dataclass, field
from enum import StrEnum

from .loader import loader, LazyLoadModel
from .const import *

# ToDo: We have a hard code of ObjectMeta's version here.
#  We should dynamically build this import depending on future k8s meta versions.
from k8s_models.api_v1.io.k8s.apimachinery.pkg.apis.meta import ObjectMeta, ListMeta


@loader
@dataclass(kw_only=True, frozen=True)
class K8sResource(LazyLoadModel):
    apiVersion: str
    kind: str
    metadata: ObjectMeta

    api_path_: ClassVar[str]
    plural_: ClassVar[str]
    group_: ClassVar[Optional[str]]
    kind_: ClassVar[str]
    apiVersion_: ClassVar[str]
    patch_strategies_: ClassVar[Set[PatchRequestType]]
    is_namespaced_: ClassVar[bool]


@loader
@dataclass(kw_only=True, frozen=True)
class K8sResourceList[ResourceT: K8sResource](K8sResource):
    items: List[ResourceT]
    apiVersion: str = 'v1'
    kind: str = f'{ResourceT.__class__.__name__}List'
    metadata: ListMeta = field(default_factory=ListMeta)
