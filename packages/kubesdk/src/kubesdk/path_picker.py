from __future__ import annotations
from typing import Any, Generic, Mapping, Sequence, Type, TypeVar, Union, overload, cast

from ._patch.json_patch import escape_json_path_pointer_token


RootT = TypeVar("RootT")
LeafT = TypeVar("LeafT")
PathKey = Union[str, int]


class _PathRoot:
    __slots__ = ("_segments",)

    def __init__(self, segments: Sequence[PathKey] | None = None):
        object.__setattr__(self, "_segments", segments or [])

    def __getattr__(self, name: str) -> _PathRoot:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if name.startswith("_"):
            raise AttributeError(name)
        return _PathRoot([*self._segments, name])

    def __getitem__(self, key: PathKey) -> _PathRoot:
        if isinstance(key, int):
            if key < 0:
                raise IndexError("Negative indices are not allowed in JSON Pointer paths")
            return _PathRoot([*self._segments, key])
        if isinstance(key, str):
            return _PathRoot([*self._segments, key])
        raise TypeError(f"Unsupported key type for path segment: {type(key)!r}")

    def __call__(self, *args: Any, **kwargs: Any) -> _PathRoot:
        raise TypeError("Calling values in a path expression is not supported")

    def __repr__(self) -> str:
        return f"_PathRoot({self._segments!r})"


class PathResolutionError(Exception):
    def __init__(self, obj: Any, segment: PathKey, index: int, segments: Sequence[PathKey], cause: Exception | None = None):
        self.obj = obj
        self.segment = segment
        self.index = index
        self.segments = segments
        self.cause = cause
        ptr_so_far = "/" + "/".join(
            str(s) if isinstance(s, int)
            else escape_json_path_pointer_token(s) for s in segments[:index])
        super().__init__(f"Failed to resolve segment {segment!r} at position {index} after {ptr_so_far or '(root)'} "
                         f"on object of type {type(obj).__name__}: {cause or 'unavailable'}")


class PathPicker(Generic[LeafT]):
    __slots__ = ("segments",)

    def __init__(self, segments: Sequence[PathKey]):
        self.segments = segments

    def json_path_pointer(self) -> str:
        if not self.segments:
            return ""
        parts: list[str] = []
        for seg in self.segments:
            if isinstance(seg, int):
                parts.append(str(seg))
            else:
                parts.append(escape_json_path_pointer_token(seg))
        return "/" + "/".join(parts)

    def __str__(self) -> str:
        return self.json_path_pointer()

    def pick_(self, obj: Any) -> LeafT:
        cur: Any = obj
        for i, seg in enumerate(self.segments):
            try:
                if isinstance(seg, int):
                    if isinstance(cur, (list, tuple)):
                        cur = cur[seg]
                    else:
                        cur = cur[seg]
                else:
                    if isinstance(cur, Mapping):
                        if seg in cur:
                            cur = cur[seg]
                        else:
                            try:
                                cur = getattr(cur, seg)
                            except AttributeError as e_attr:
                                try:
                                    cur = cur[seg]
                                except Exception as e_item:
                                    raise PathResolutionError(
                                        obj=cur, segment=seg, index=i, segments=self.segments, cause=e_item
                                    ) from e_item
                    else:
                        try:
                            cur = getattr(cur, seg)
                        except AttributeError as e_attr:
                            try:
                                cur = cur[seg]
                            except Exception as e_item:
                                raise PathResolutionError(
                                    obj=cur, segment=seg, index=i, segments=self.segments, cause=e_item
                                ) from e_item
            except IndexError as e:
                raise PathResolutionError(obj=cur, segment=seg, index=i, segments=self.segments, cause=e) from e
            except KeyError as e:
                raise PathResolutionError(obj=cur, segment=seg, index=i, segments=self.segments, cause=e) from e
            except TypeError as e:
                raise PathResolutionError(obj=cur, segment=seg, index=i, segments=self.segments, cause=e) from e
        return cast(LeafT, cur)


def from_root_(cls: Type[RootT]) -> RootT:
    """
    :param cls: Type of the object (normally - class) to point a path on its instances.
    :return: A typed proxy root for building a PathPicker from it.
    """
    return cast(RootT, _PathRoot())


@overload
def path_(expr: LeafT) -> PathPicker[LeafT]: ...
@overload
def path_(expr: _PathRoot) -> PathPicker[Any]: ...
def path_(expr: Union[LeafT, _PathRoot]) -> PathPicker:
    """
    :param expr: A typed proxy with the target path.
    :return: A PathPicker object, which can be used to pick values on instances of the rooted type.
    """
    return PathPicker(expr._segments)
