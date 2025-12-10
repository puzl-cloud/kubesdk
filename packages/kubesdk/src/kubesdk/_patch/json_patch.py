"""
Utils to work with JSON Patch (RFC 6902).
"""
from __future__ import annotations

from typing import Any
import copy


def _list_opcodes(old_list: list[Any], new_list: list[Any]) -> list[tuple[str, int, int, int, int]]:
    """
    Compute edit opcodes between two lists using dynamic programming with element equality.
    Returns a list of tuples (tag, i1, i2, j1, j2) similar to difflib.get_opcodes().
    Supports unhashable elements (dicts/lists) by using equality comparisons only.
    """
    n, m = len(old_list), len(new_list)

    # DP matrix for edit distance with costs: equal=0, replace=1, delete=1, insert=1
    distance = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        distance[i][0] = i
    for j in range(1, m + 1):
        distance[0][j] = j

    for i in range(1, n + 1):
        old_item = old_list[i - 1]
        for j in range(1, m + 1):
            new_item = new_list[j - 1]
            cost = 0 if old_item == new_item else 1
            distance[i][j] = min(
                distance[i - 1][j] + 1,        # delete
                distance[i][j - 1] + 1,        # insert
                distance[i - 1][j - 1] + cost  # replace/equal
            )

    # Backtrace to produce opcodes
    i, j = n, m
    opcodes: list[tuple[str, int, int, int, int]] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and old_list[i - 1] == new_list[j - 1] and distance[i][j] == distance[i - 1][j - 1]:
            i2, j2 = i, j
            while i > 0 and j > 0 and old_list[i - 1] == new_list[j - 1] and distance[i][j] == distance[i - 1][j - 1]:
                i -= 1
                j -= 1
            opcodes.append(("equal", i, i2, j, j2))
        elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
            opcodes.append(("replace", i - 1, i, j - 1, j))
            i -= 1
            j -= 1
        elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
            opcodes.append(("delete", i - 1, i, j, j))
            i -= 1
        else:
            opcodes.append(("insert", i, i, j - 1, j))
            j -= 1

    opcodes.reverse()

    # Merge adjacent same-tag ops
    merged: list[tuple[str, int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if merged and merged[-1][0] == tag:
            _, mi1, mi2, mj1, mj2 = merged[-1]
            if mi2 == i1 and mj2 == j1:
                merged[-1] = (tag, mi1, i2, mj1, j2)
            else:
                merged.append((tag, i1, i2, j1, j2))
        else:
            merged.append((tag, i1, i2, j1, j2))

    return merged


Json = list | dict
Op = dict[str, Any]

def escape_json_path_pointer_token(token: str) -> str:
    # RFC 6901 escaping: "~" -> "~0", "/" -> "~1"
    return token.replace("~", "~0").replace("/", "~1")

def _join_path(base_path: str, token: str) -> str:
    if base_path == "" or base_path == "/":
        return "/" + escape_json_path_pointer_token(token)
    return base_path + "/" + escape_json_path_pointer_token(token)

def _diff_dict(old_map: dict[str, Any], new_map: dict[str, Any], json_pointer: str, patch_ops: list[Op]) -> None:
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # removals
    for key in sorted(old_keys - new_keys):
        patch_ops.append({"op": "remove", "path": _join_path(json_pointer, key)})

    # additions
    for key in sorted(new_keys - old_keys):
        patch_ops.append({"op": "add", "path": _join_path(json_pointer, key), "value": copy.deepcopy(new_map[key])})

    # updates
    for key in sorted(old_keys & new_keys):
        _diff_any(old_map[key], new_map[key], _join_path(json_pointer, key), patch_ops)

def _diff_list(old_list: list[Any], new_list: list[Any], json_pointer: str, patch_ops: list[Op]) -> None:
    """
    Build a patch using add/remove/replace operations on indices.
    """
    opcodes = _list_opcodes(old_list, new_list)

    # Walk from end to start so earlier indices remain valid.
    for tag, i1, i2, j1, j2 in reversed(opcodes):
        if tag == "equal":
            continue
        if tag == "replace":
            new_slice = new_list[j1:j2]
            for k, value in enumerate(new_slice):
                patch_ops.append({"op": "replace", "path": f"{json_pointer}/{i1 + k}", "value": copy.deepcopy(value)})
        elif tag == "delete":
            for idx in range(i2 - 1, i1 - 1, -1):
                patch_ops.append({"op": "remove", "path": f"{json_pointer}/{idx}"})
        elif tag == "insert":
            for k, value in enumerate(new_list[j1:j2]):
                patch_ops.append({"op": "add", "path": f"{json_pointer}/{i1 + k}", "value": copy.deepcopy(value)})
        else:
            # Should not happen
            raise RuntimeError(f"Unexpected opcode tag: {tag}")

def _diff_any(old_value: Any, new_value: Any, json_pointer: str, patch_ops: list[Op]) -> None:
    if old_value == new_value:
        return

    # Different types -> replace
    if type(old_value) is not type(new_value):
        patch_ops.append({"op": "replace", "path": json_pointer, "value": copy.deepcopy(new_value)})
        return

    # Same types
    if isinstance(old_value, dict):
        _diff_dict(old_value, new_value, json_pointer, patch_ops)
    elif isinstance(old_value, list):
        _diff_list(old_value, new_value, json_pointer, patch_ops)
    else:
        # scalars differ -> replace
        patch_ops.append({"op": "replace", "path": json_pointer, "value": copy.deepcopy(new_value)})

def json_patch_from_diff(old_doc: Json, new_doc: Json) -> list[Op]:
    """
    Compute a JSON Patch that transforms old_doc into new_doc.
    :param old_doc: Old version of the object
    :param new_doc: Version of the object after the JSON Patch applied
    :returns:
        RFC6902 JSON Patch object
    """
    patch_ops: list[Op] = []

    # Special case: root replacement when types differ
    if type(old_doc) is not type(new_doc):
        patch_ops.append({"op": "replace", "path": "/", "value": copy.deepcopy(new_doc)})
        return patch_ops

    if isinstance(old_doc, dict):
        _diff_dict(old_doc, new_doc, "", patch_ops)
    elif isinstance(old_doc, list):
        _diff_list(old_doc, new_doc, "", patch_ops)
    else:
        if old_doc != new_doc:
            patch_ops.append({"op": "replace", "path": "/", "value": copy.deepcopy(new_doc)})

    return patch_ops


class JsonPointerError(Exception): pass
class JsonPatchTestFailed(Exception): pass


def _parse_pointer(json_pointer: str) -> tuple[bool, list[str]]:
    if not json_pointer:
        raise JsonPointerError("Empty JSON Pointer")
    if json_pointer == "/":
        return True, []
    if not json_pointer.startswith("/"):
        raise JsonPointerError(f"Invalid JSON Pointer: {json_pointer}")
    tokens = json_pointer.split("/")[1:]
    decoded = [t.replace("~1", "/").replace("~0", "~") for t in tokens]
    return False, decoded

def _get_at_pointer(root: Any, tokens: list[str]) -> Any:
    """Return the value located at the JSON Pointer tokens."""
    if not tokens:
        return root
    node = root
    for token in tokens:
        if isinstance(node, dict):
            node = node[token]
        elif isinstance(node, list):
            if token == '-':
                raise JsonPointerError("'-' is not a valid index for retrieval")
            try:
                idx = int(token)
            except Exception as e:
                raise JsonPointerError("Invalid array index") from e
            node = node[idx]
        else:
            raise JsonPointerError("Cannot traverse into scalar")
    return node

def _resolve_parent(root: Any, tokens: list[str]) -> tuple[Any, str]:
    """
    Resolve to the parent container of the final token.
    Returns (parent_node, last_token).
    """
    if not tokens:
        return None, ""
    current_node = root
    for token in tokens[:-1]:
        if isinstance(current_node, dict):
            current_node = current_node[token]
        elif isinstance(current_node, list):
            idx = int(token)
            current_node = current_node[idx]
        else:
            raise JsonPointerError("Cannot traverse into scalar")
    return current_node, tokens[-1]


def apply_patch(document: Json, patch_ops: list[Op]) -> Json:
    """
    Apply JSON Patch operations (RFC 6902): add, remove, replace, move, copy, test.
    Returns a deep-copied patched document.
    Notes:
      - Root removal is disallowed (raises JsonPointerError), matching previous behavior.
      - Array appends use "-" (allowed for add/copy/move destinations; not valid for remove/replace/test).
    """
    result = copy.deepcopy(document)

    for op in patch_ops:
        operation = op["op"]
        path = op["path"]
        is_root, tokens = _parse_pointer(path)

        if operation == "test":
            expected = op.get("value")
            target_value = result if is_root else _get_at_pointer(result, tokens)
            if target_value != expected:
                raise JsonPatchTestFailed(f"Test failed at path {path}")
            continue

        if operation == "copy":
            from_path = op["from"]
            from_is_root, from_tokens = _parse_pointer(from_path)
            source_value = result if from_is_root else _get_at_pointer(result, from_tokens)
            value_to_set = copy.deepcopy(source_value)

            if is_root:
                result = value_to_set
            else:
                parent_node, last_token = _resolve_parent(result, tokens)
                if isinstance(parent_node, dict):
                    parent_node[last_token] = value_to_set
                elif isinstance(parent_node, list):
                    if last_token == '-':
                        parent_node.append(value_to_set)
                    else:
                        try:
                            idx = int(last_token)
                        except Exception as e:
                            raise JsonPointerError("Invalid array index") from e
                        parent_node.insert(idx, value_to_set)
                else:
                    raise JsonPointerError("Invalid copy target")
            continue

        if operation == "move":
            from_path = op["from"]
            from_is_root, from_tokens = _parse_pointer(from_path)
            if from_is_root:
                raise JsonPointerError("Moving the root is not supported")

            source_parent, source_last = _resolve_parent(result, from_tokens)
            source_index = 0  # we define it here to make linter happy
            if isinstance(source_parent, dict):
                moving_value = source_parent[source_last]
            elif isinstance(source_parent, list):
                try:
                    source_index = int(source_last)
                except Exception as e:
                    raise JsonPointerError("Invalid array index") from e
                moving_value = source_parent[source_index]
            else:
                raise JsonPointerError("Invalid move source")

            if is_root:
                if isinstance(source_parent, dict):
                    del source_parent[source_last]
                else:
                    del source_parent[source_index]
                result = copy.deepcopy(moving_value)
                continue

            dest_parent, dest_last = _resolve_parent(result, tokens)
            if isinstance(dest_parent, dict):
                if isinstance(source_parent, dict):
                    del source_parent[source_last]
                else:
                    del source_parent[source_index]
                dest_parent[dest_last] = moving_value
            elif isinstance(dest_parent, list):
                if dest_last == '-':
                    dest_index = len(dest_parent)
                else:
                    try:
                        dest_index = int(dest_last)
                    except Exception as e:
                        raise JsonPointerError("Invalid array index") from e

                same_list = (dest_parent is source_parent)
                if isinstance(source_parent, list):
                    if same_list and source_index < dest_index:
                        adjusted_dest_index = dest_index - 1
                    else:
                        adjusted_dest_index = dest_index
                else:
                    adjusted_dest_index = dest_index

                if isinstance(source_parent, dict):
                    del source_parent[source_last]
                else:
                    del source_parent[source_index]

                dest_parent.insert(adjusted_dest_index, moving_value)
            else:
                raise JsonPointerError("Invalid move target")
            continue

        if operation == "remove":
            if is_root:
                raise JsonPointerError("Removing the root is not supported")
            parent_node, last_token = _resolve_parent(result, tokens)
            if isinstance(parent_node, dict):
                parent_node.pop(last_token, None)
            elif isinstance(parent_node, list):
                if last_token == '-':
                    raise JsonPointerError("'-' is not valid for remove")
                try:
                    idx = int(last_token)
                except Exception as e:
                    raise JsonPointerError("Invalid array index") from e
                del parent_node[idx]
            else:
                raise JsonPointerError("Invalid remove target")
            continue

        if operation == "add":
            value = copy.deepcopy(op["value"])
            if is_root:
                result = value
            else:
                parent_node, last_token = _resolve_parent(result, tokens)
                if isinstance(parent_node, dict):
                    parent_node[last_token] = value
                elif isinstance(parent_node, list):
                    if last_token == '-':
                        parent_node.append(value)
                    else:
                        try:
                            idx = int(last_token)
                        except Exception as e:
                            raise JsonPointerError("Invalid array index") from e
                        parent_node.insert(idx, value)
                else:
                    raise JsonPointerError("Invalid add target")
            continue

        if operation == "replace":
            value = copy.deepcopy(op["value"])
            if is_root:
                result = value
            else:
                parent_node, last_token = _resolve_parent(result, tokens)
                if isinstance(parent_node, dict):
                    parent_node[last_token] = value
                elif isinstance(parent_node, list):
                    if last_token == '-':
                        raise JsonPointerError("'-' is not valid for replace")
                    try:
                        idx = int(last_token)
                    except Exception as e:
                        raise JsonPointerError("Invalid array index") from e
                    parent_node[idx] = value
                else:
                    raise JsonPointerError("Invalid replace target")
            continue

        raise NotImplementedError(f"Unsupported op: {operation}")

    return result


def _list_item_roots_for_path(latest_known_resource: dict[str, Any], segments: list[str]) -> list[list[str]]:
    """
    Detect which list item roots are affected by a path based on actual types in latest_known_resource.

    - If traversal steps into a list (numeric segment on a list) -> return that single item root.
    - Else, if the resolved node is a list (path points at list root) -> return roots for all current items.
    - Else -> empty list (no list involvement).
    """
    cur = latest_known_resource
    for i, s in enumerate(segments):
        if isinstance(cur, list):
            # Real list: only numeric seg is a valid index
            if s.isdigit():
                idx = int(s)
                if 0 <= idx < len(cur):
                    # this op targets inside this list item
                    return [segments[:i+1]]
                else:
                    # invalid index -> no tests
                    return []
            else:
                # non-numeric segment on a list -> not a list path for our purposes
                return []
        elif isinstance(cur, dict):
            if s in cur:
                cur = cur[s]
            else:
                # missing key -> no tests
                return []
        else:
            # scalar mid-path -> no tests
            return []

    # Completed walk without stepping into a list element.
    # If the resolved node is a list, treat the path as targeting the list root.
    if isinstance(cur, list):
        return [segments + [str(i)] for i in range(len(cur))]
    return []


def _flatten_leaves(node: Any, base: list[str]) -> list[tuple[list[str], Any]]:
    """Return (path, value) for all scalar leaves under node, starting from base path."""
    out: list[tuple[list[str], Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_flatten_leaves(v, base + [k]))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_flatten_leaves(v, base + [str(i)]))
    else:
        out.append((base, node))
    return out


def guard_lists_from_json_patch_replacement(json_patch: list[dict[str, Any]], latest_known_resource: dict[str, Any]) \
        -> list[dict[str, Any]]:
    """
    Inserts `test` operations guarding list items touched by the given JSON Patch.

    For each op:
      - If path targets inside a list item -> add `test` for all leaf key/values under that item.
      - Else if path targets a list root -> add `test` for leaves under every item currently in the list.

    :param json_patch: RFC6902 JSON Patch object
    :param latest_known_resource: version of the object which is supposed to be patched
    :returns:
        RFC6902 JSON Patch object
    """
    new_json_patch: list[dict[str, Any]] = []
    tested_roots: set[str] = set()

    for op in json_patch:
        pointer = op.get("path", "")
        if pointer is None:
            segments = []
        else:
            pointer = pointer.lstrip("/")
            segments = [] if pointer == "" else [s for s in pointer.split("/") if s != ""]
        item_roots = _list_item_roots_for_path(latest_known_resource, segments)

        # Insert tests once per item root
        for root in item_roots:
            root_ptr = "/" + "/".join(root)
            if root_ptr in tested_roots:
                continue
            tested_roots.add(root_ptr)

            item_node = _get_at_pointer(latest_known_resource, root)
            if item_node is not None:
                for leaf_path, leaf_val in _flatten_leaves(item_node, root):
                    new_json_patch.append({
                        "op": "test",
                        "path": "/" + "/".join(leaf_path),
                        "value": leaf_val
                    })

        new_json_patch.append(op)

    return new_json_patch
