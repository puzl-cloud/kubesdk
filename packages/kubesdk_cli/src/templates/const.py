from typing import Literal
from enum import StrEnum


# We do not add None to scalars because it makes no sense for our dynamic type loading.
# We will never have a field of None type in practice, but may receive None as a result of get_origin() call,
# which can be misleading.
SCALAR_TYPES = [str, int, float, bool, complex, bytes, bytearray, Literal]


class PatchRequestType(StrEnum):
    apply = 'application/apply-patch+yaml'
    json = 'application/json-patch+json'
    merge = 'application/merge-patch+json'
    strategic_merge = 'application/strategic-merge-patch+json'


class FieldPatchStrategy(StrEnum):
    retainKeys = "retainKeys"
    merge = "merge"
    replace = "replace"


PATCH_MERGE_KEY = 'x-kubernetes-patch-merge-key'
PATCH_STRATEGY = 'x-kubernetes-patch-strategy'

EXCLUDE_FIELD_META_KEY = "exclude_from_dict"
