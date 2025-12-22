from typing import Literal
try:
    from enum import StrEnum  # Python 3.11+
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass


# We do not add None to scalars because it makes no sense for our dynamic type loading.
# We will never have a field of None type in practice, but may receive None as a result of get_origin() call,
# which can be misleading.
SCALAR_TYPES = [str, int, float, bool, complex, bytes, bytearray, Literal]


class PatchRequestType(StrEnum):
    _legacy_json = "application/json"  # Swagger schema legacy
    server_side_cbor = "application/apply-patch+cbor"
    server_side = 'application/apply-patch+yaml'
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
