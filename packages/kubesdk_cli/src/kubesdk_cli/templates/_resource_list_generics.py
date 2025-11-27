from typing import TypeVar, Generic, ClassVar
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary
from threading import RLock

# noinspection ALL
from ._k8s_resource_base import K8sResource, ListMeta, _bind_class_vars_from_original_kind


ResourceT = TypeVar("ResourceT", bound=K8sResource)


@dataclass(slots=True, kw_only=True, frozen=True)
class K8sResourceList(Generic[ResourceT], K8sResource):
    items: list[ResourceT]
    metadata: ListMeta = field(default_factory=ListMeta)

    _type_cache: ClassVar[WeakKeyDictionary] = WeakKeyDictionary()
    _type_cache_lock: ClassVar[RLock] = RLock()

    # Bind apiVersion to the List from the original kind dynamically
    def __class_getitem__(cls, params): return _bind_class_vars_from_original_kind(cls, params)
