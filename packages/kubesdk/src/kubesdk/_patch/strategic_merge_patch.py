from dataclasses import is_dataclass, fields
from typing import Any, Optional
import copy

from kube_models.const import PatchRequestType, FieldPatchStrategy, PATCH_STRATEGY, PATCH_MERGE_KEY
from kube_models.resource import K8sResource

from .json_patch import apply_patch


def _is_primitive(x: Any) -> bool: return not isinstance(x, (dict, list))
def _unescape(segment: str) -> str: return segment.replace("~1", "/").replace("~0", "~")


def _split_pointer(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError(f"Invalid JSON Pointer (must start with '/'): {path}")
    if path == "/":
        return []
    return [_unescape(p) for p in path.lstrip("/").split("/")]


def _get_by_pointer(doc: Any, path: str) -> Any:
    parts = _split_pointer(path)
    current = doc
    for p in parts:
        if isinstance(current, list):
            if p == "-":
                raise KeyError("'-' not valid for get")
            current = current[int(p)]
        else:
            current = current[p]
    return current


def _set_by_pointer(doc: Any, path: str, value: Any) -> None:
    parts = _split_pointer(path)
    if not parts:
        raise ValueError("Cannot replace entire document by pointer")
    cur = doc
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(cur, list):
            idx = len(cur) if p == "-" else int(p)
            if last:
                if p == "-":
                    cur.append(value)
                else:
                    if idx == len(cur):
                        cur.append(value)
                    else:
                        cur.insert(idx, value)
                return
            cur = cur[idx]
        else:
            if last:
                cur[p] = value
                return
            if p not in cur or cur[p] is None:
                nxt = {} if not parts[i + 1].isdigit() else []
                cur[p] = nxt
            cur = cur[p]


def _remove_by_pointer(doc: Any, path: str) -> Any:
    parts = _split_pointer(path)
    if not parts:
        raise ValueError("Cannot remove entire document")
    cur = doc
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(cur, list):
            idx = int(p)
            if last:
                return cur.pop(idx)
            cur = cur[idx]
        else:
            if last:
                return cur.pop(p)
            cur = cur[p]


def _is_int_token(tok: str) -> bool:
    try:
        int(tok)
        return True
    except ValueError:
        return False


def _lookup_merge_info_by_type(path_tokens: tuple[str, ...], root: K8sResource) -> Optional[dict[str, str]]:
    current = root
    i = 0
    last_list_meta, last_field_meta = None, None
    while i < len(path_tokens):
        token = path_tokens[i]
        if isinstance(current, list):
            if token == "-" or _is_int_token(token):
                i += 1
                continue
        f = None
        if is_dataclass(current):
            for class_field in fields(current):
                if class_field.name == token:
                    f = class_field
                    break

        if f is None:
            if not isinstance(current, dict):
                return last_list_meta or last_field_meta
            current = current.get(token)
            i += 1
            continue

        if f.metadata:
            # Merging is possible
            if PATCH_MERGE_KEY in f.metadata or (f.metadata.get(PATCH_STRATEGY) == FieldPatchStrategy.merge):
                last_list_meta = dict(f.metadata)

            # Merging is not possible
            if PATCH_STRATEGY in f.metadata and f.metadata.get(PATCH_STRATEGY) != FieldPatchStrategy.merge:
                last_field_meta = dict(f.metadata)

        current = getattr(current, token, None)
        i += 1
    return last_list_meta or last_field_meta


def _strategic_merge_diff(original: dict, target: dict, root: K8sResource, path: tuple[str, ...] = None) \
        -> dict[str, Any]:
    """
    Compute a Strategic Merge Patch (SMP) object that transforms `original` into `target`.
    
    Behavior:
    
        1. If both sides are dicts:
        
            * Iterate the union of keys (sorted for determinism).
            
            * For list->list fields, delegate to `_list_diff` and attach any of:
            
                - direct list patch value
                - $patch/<field>: "replace"
                - $setElementOrder/<field>
                - $deleteFromPrimitiveList/<field>
            
            * For dict->dict fields:
            
                - If the field's metadata has patch-strategy=retainKeys, emit a nested object
                with `$retainKeys` listing all keys kept in the target and include nested
                diffs for changed keys only.
                
                - Otherwise, recurse and include nested diffs if any.
            
            * For scalars or type-changed fields, set the field to the target value.
            
            * After processing, explicitly null out keys that exist only in `original` to signal deletion.
        
        2. If either side is not a dict, return an empty patch (caller handles replacement).

    Returns:
      A dict representing the SMP for this level (possibly empty).
    """
    result: dict[str, Any] = {}

    if path is None:
        path = ()

    # Only dict-vs-dict is merged at this level; everything else is handled by the caller.
    if isinstance(original, dict) and isinstance(target, dict):
        original_keys: set[str] = set(original.keys())
        target_keys: set[str] = set(target.keys())

        for key in sorted(original_keys | target_keys):
            original_value = original.get(key)
            target_value = target.get(key)

            # No change
            if original_value == target_value:
                continue

            # List field
            if isinstance(original_value, list) and isinstance(target_value, list):
                patch_value, order_directive, replace_flag, delete_from_primitive = _list_diff(
                    original_value, target_value, root, path + (key,)
                )
                if patch_value is not None:
                    result[key] = patch_value
                if replace_flag:
                    result[f"$patch/{key}"] = FieldPatchStrategy.replace
                if order_directive is not None:
                    result[f"$setElementOrder/{key}"] = order_directive
                if delete_from_primitive is not None:
                    result[f"$deleteFromPrimitiveList/{key}"] = delete_from_primitive
                continue

            # dict field
            if isinstance(original_value, dict) and isinstance(target_value, dict):
                field_meta = _lookup_merge_info_by_type(path + (key,), root)

                # retainKeys strategy
                if field_meta and field_meta.get(PATCH_STRATEGY) == FieldPatchStrategy.retainKeys:
                    retained_keys = sorted(target_value.keys())
                    nested_patch: dict[str, Any] = {}

                    for sub_key in sorted(set(target_value.keys()) | set(original_value.keys())):
                        # Keys absent in target are implicitly dropped by retainKeys
                        if sub_key not in target_value:
                            continue

                        o2 = original_value.get(sub_key)
                        t2 = target_value.get(sub_key)
                        if o2 == t2:
                            continue

                        if isinstance(o2, dict) and isinstance(t2, dict):
                            sub_diff = _strategic_merge_diff(o2, t2, root, path + (key, str(sub_key)))
                            if sub_diff:
                                nested_patch[sub_key] = sub_diff
                        elif isinstance(o2, list) and isinstance(t2, list):
                            sub_list, sub_order, sub_replace, sub_del_prim = _list_diff(
                                o2, t2, root, path + (key, str(sub_key))
                            )
                            if sub_list is not None:
                                nested_patch[sub_key] = sub_list
                            if sub_replace:
                                nested_patch[f"$patch/{sub_key}"] = FieldPatchStrategy.replace
                            if sub_order is not None:
                                nested_patch[f"$setElementOrder/{sub_key}"] = sub_order
                            if sub_del_prim is not None:
                                nested_patch[f"$deleteFromPrimitiveList/{sub_key}"] = sub_del_prim
                        else:
                            nested_patch[sub_key] = copy.deepcopy(t2)

                    nested_patch["$retainKeys"] = retained_keys
                    result[key] = nested_patch if nested_patch else {"$retainKeys": retained_keys}
                    continue

                # Regular nested merge
                nested = _strategic_merge_diff(original_value, target_value, root, path + (key,))
                if nested:
                    result[key] = nested
                continue

            # Primitive or type-mismatch -> replace
            result[key] = copy.deepcopy(target_value)

        # Explicitly null out keys present only in `original` (deletions)
        for key in sorted(original_keys - target_keys):
            result[key] = None

        return result

    # Not merging at this level (handled by caller)
    return {}


def _list_diff(original_list: list, target_list: list, root: K8sResource, path: tuple[str, ...]) -> \
        tuple[Optional[list], Optional[list], bool, Optional[list]]:
    """
    Compute the strategic-merge delta between two lists.

    Returns a 4-tuple:
      1) patch_value: list for SMP value (e.g., keyed element updates) or None if no change
      2) order_directive: list of {"<mergeKey>": value} for $setElementOrder/<field>, or None
      3) replace_flag: True if caller must set $patch/<field>: "replace"
      4) delete_from_primitive: list of primitives for $deleteFromPrimitiveList/<field>, or None
    """

    merge_info = _lookup_merge_info_by_type(path, root)

    # Detect whether lists are lists-of-dicts on each side.
    orig_all_dicts = all(isinstance(x, dict) for x in original_list) \
        if original_list else all(isinstance(x, dict) for x in target_list)
    target_all_dicts = all(isinstance(x, dict) for x in target_list) \
        if target_list else all(isinstance(x, dict) for x in original_list)

    # Keyed list merge: field's patch-strategy is merge and patch-merge-key present
    if merge_info \
            and merge_info.get(PATCH_STRATEGY) == FieldPatchStrategy.merge \
            and orig_all_dicts \
            and target_all_dicts:
        merge_key = merge_info.get(PATCH_MERGE_KEY, "")
        original_map = {
            item.get(merge_key): item for item in original_list if isinstance(item, dict) and merge_key in item
        }
        target_map   = {
            item.get(merge_key): item for item in target_list if isinstance(item, dict) and merge_key in item
        }
        patch_elems: list[Any] = []

        # Additions / modifications
        for k in target_map:
            if k in original_map:
                sub_patch = _strategic_merge_diff(original_map[k], target_map[k], root, path + (merge_key, str(k)))
                if sub_patch:
                    sub_patch[merge_key] = k
                    patch_elems.append(sub_patch)
            else:
                patch_elems.append(copy.deepcopy(target_map[k]))

        # Deletions
        for k in original_map:
            if k not in target_map:
                patch_elems.append({merge_key: k, "$patch": "delete"})

        def order_keys(seq: list[Any]) -> list[Any]:
            return [item.get(merge_key) for item in seq if isinstance(item, dict) and merge_key in item]

        order_changed = order_keys(original_list) != order_keys(target_list)
        order_directive = [{merge_key: v} for v in order_keys(target_list)] if order_changed else None

        if not patch_elems and not order_directive:
            return None, None, False, None

        return (patch_elems if patch_elems else None), order_directive, False, None

    both_primitive = all(_is_primitive(x) for x in original_list) and all(_is_primitive(x) for x in target_list)
    if both_primitive:
        additions = [x for x in target_list if x not in original_list]
        deletions = [x for x in original_list if x not in target_list]
        # If there are only deletions, prefer $deleteFromPrimitiveList.
        if deletions and not additions:
            seen = set()
            deduped_deletions: list[Any] = []
            for v in deletions:
                if v not in seen:
                    seen.add(v)
                    deduped_deletions.append(v)
            return None, None, False, deduped_deletions

    # Fallback: replace entire list if changed, otherwise no-op.
    if original_list != target_list:
        return copy.deepcopy(target_list), None, True, None

    return None, None, False, None


def jsonpatch_to_smp(resource: K8sResource, json_patch: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Converts RFC6902 jsonPatch object into Kubernetes Strategic Merge Patch according to this proposal
    https://github.com/kubernetes/community/blob/master/contributors/devel/sig-api-machinery/strategic-merge-patch.md
    """
    if PatchRequestType.strategic_merge not in resource.patch_strategies_:
        raise TypeError("Resource does not support strategic merge patch")
    original_dict = resource.to_dict()
    target = apply_patch(resource.to_dict(), json_patch)
    return _strategic_merge_diff(original_dict, target, resource)
