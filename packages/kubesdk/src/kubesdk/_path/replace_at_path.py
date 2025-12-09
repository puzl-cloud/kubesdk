from __future__ import annotations

from dataclasses import is_dataclass, replace as dc_replace
from typing import Any, TypeVar, Sequence, cast
from collections.abc import MutableMapping

from .picker import PathKey, PathPicker, _resolve_segment

_ObjectT = TypeVar("_ObjectT")
_ValueT = TypeVar("_ValueT")


def _replace_recursive(
    current: Any,
    remaining_segments: Sequence[PathKey],
    new_value: Any,
    full_segments: Sequence[PathKey],
    index: int
) -> Any:
    if not remaining_segments:
        return new_value

    segment, *tail = remaining_segments

    # Read child
    child = _resolve_segment(current, segment, index=index, segments=full_segments)
    updated_child = _replace_recursive(child, tail, new_value, full_segments, index + 1)

    # Now decide how to write back based on container & segment types
    if isinstance(segment, int):
        if isinstance(current, list):
            new_list = list(current)
            new_list[segment] = updated_child
            return new_list
        if isinstance(current, tuple):
            new_items = list(current)
            new_items[segment] = updated_child
            return type(current)(new_items)
        if isinstance(current, MutableMapping):
            new_mapping = dict(current)
            new_mapping[segment] = updated_child
            return cast(type(current), new_mapping)

        # generic __setitem__ container
        current[segment] = updated_child
        return current

    if isinstance(current, MutableMapping) and segment in current:
        new_mapping = dict(current)
        new_mapping[segment] = updated_child
        return cast(type(current), new_mapping)

    if hasattr(current, segment):
        if is_dataclass(current):
            # Never apply in_place to dataclasses
            return dc_replace(current, **{segment: updated_child})
        try:
            clone = current.__class__.__new__(current.__class__)
            if hasattr(current, "__dict__"):
                clone.__dict__.update(current.__dict__)
            else:
                clone = current
        except Exception:
            clone = current

        setattr(clone, segment, updated_child)
        return clone

    # Item access fallback (string key/index-style)
    if isinstance(current, MutableMapping):
        new_mapping = dict(current)
        new_mapping[segment] = updated_child
        return cast(type(current), new_mapping)

    # Best-effort for non-mapping
    current[segment] = updated_child
    return current


def replace_(obj: _ObjectT, path: PathPicker[_ValueT], new_value: _ValueT) -> _ObjectT:
    """
    Deep analogue of dataclasses.replace() using PathPicker.
    Unlike dataclasses.replace(), it always copies values of the mapping fields.

    :param obj: Object to make a replacement on.
    :param path: Path pointing to the leaf to replace.
    :param new_value: New value to set at the leaf.
    :returns: New object with the value at ``path`` replaced.
    :raises PathResolutionError: If any path segment cannot be resolved.
    """
    return cast(_ObjectT, _replace_recursive(obj, path.segments, new_value, path.segments, 0))
