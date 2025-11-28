from __future__ import annotations

import sys
import logging
from datetime import datetime
from base64 import b64encode
import json
from dataclasses import is_dataclass, fields, field, dataclass, Field
from typing import *
from types import UnionType

if sys.version_info < (3, 11):
    from typing_extensions import Self

from .const import *


_CACHED_TYPES = {}

_LOAD_TYPES_ON_INIT = "__should_load"
_LOAD_LAZY_FIELD = "_lazy"
_LAZY_SRC_FIELD = "_lazy_src"


def _supports_lazy_load(obj: Any) -> bool:
    if not is_dataclass(obj):
        return False
    all_fields = fields(obj)
    return bool([f for f in all_fields if f.name == _LAZY_SRC_FIELD])


def _inject_lazy_load(obj_type: type[Any], kwargs: dict[str, Any], use_lazy: bool) -> dict[str, Any]:
    if _supports_lazy_load(obj_type) and use_lazy:
        return kwargs | {_LOAD_LAZY_FIELD: True, _LOAD_TYPES_ON_INIT: True}
    return kwargs


def _pep604_to_union(type_name: str) -> str:
    """
    Convert a PEP 604 union expression in string form (e.g. "str | None")
    into a typing-style Union[...] string (e.g. "Union[str, None]").

    - If there's no top-level '|' it returns the input unchanged.
    - If it's already of the form Union[...], it returns it unchanged.
    - Ignores '|' characters inside brackets/parentheses or quotes.
    """
    s = type_name.strip()
    if '|' not in s:
        return s

    def is_balanced(text: str) -> bool:
        stack, pairs = [], {')': '(', ']': '[', '}': '{'}
        for ch in text:
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if not stack or stack[-1] != pairs[ch]:
                    return False
                stack.pop()
        return not stack

    def strip_parens(x: str) -> str:
        y = x.strip()
        while y.startswith('(') and y.endswith(')') and is_balanced(y[1:-1]):
            y = y[1:-1].strip()
        return y

    s = strip_parens(s)

    def split_top_level_union(expr: str):
        parts, buf, stack = [], [], []
        pairs = {')': '(', ']': '[', '}': '{'}
        in_single = in_double = False
        prev = ''
        for ch in expr:
            if ch == "'" and not in_double and prev != '\\':
                in_single = not in_single
            elif ch == '"' and not in_single and prev != '\\':
                in_double = not in_double
            elif not in_single and not in_double:
                if ch in '([{':
                    stack.append(ch)
                elif ch in ')]}':
                    if stack and stack[-1] == pairs[ch]:
                        stack.pop()
                elif ch == '|' and not stack:
                    parts.append(strip_parens(''.join(buf).strip()))
                    buf = []
                    prev = ch
                    continue
            buf.append(ch)
            prev = ch
        parts.append(strip_parens(''.join(buf).strip()))
        return parts

    parts = split_top_level_union(s)
    if len(parts) == 1:
        return s
    return f"Union[{', '.join(parts)}]"


def _extract_real_type(tp: Any) -> Tuple[Tuple[Any], bool]:
    """
    Unwraps Optional[T] and Union[..., NoneType], returning (base_type, is_optional).

      - Optional[T] / T | None      -> (T, True)
      - Union[A, B, …]              -> (Union[A, B, …] (None removed), True if None present)
      - Everything else (incl. List[T], Dict[K, V], etc.) -> (original type, False)

    Notes:
      * Preserves complex typing objects where possible (e.g., Union[int, str]).
      * Does NOT normalize inside non-Union containers (e.g., List[Optional[int]] stays as-is).
      * No eval() used.
    """
    origin = get_origin(tp)

    # Handle Union / PEP 604 unions
    if origin is Union or (getattr(UnionType, "__mro__", ()) and origin is UnionType):
        opt = False
        new_branches = []
        for arg in get_args(tp):
            if arg is type(None):  # NoneType
                opt = True
                continue
            base_type, inner_opt = _extract_real_type(arg)
            opt = opt or inner_opt
            new_branches.append(base_type)

        if not new_branches:
            # Union[None] -> (NoneType, True)
            return (type(None),), True

        # Collapse single branch; otherwise, rebuild Union without None
        # if len(new_branches) == 1:
        #     return new_branches[0], opt
        flat = tuple(x for a in new_branches for x in (a if isinstance(a, tuple) else (a,)))
        return flat, opt

    # Everything else: leave untouched
    return (tp,), False


def _evaluate_field_by_meta(field_value: Any, metadata: Mapping) -> Any:
    try:
        return metadata.get('decoder')(field_value)
    except Exception as e:
        raise ValueError(e)


def _evaluate_value(field_type: Any, field_value: Any, use_lazy: bool = False) -> Any:
    try:
        # Preserve original arg for later
        passed_field_type = field_type

        # Use cache to avoid useless recursions every time
        if passed_field_type in _CACHED_TYPES:
            cached_field_type = _CACHED_TYPES[passed_field_type]

            # We cache None for all scalars
            if cached_field_type is None:
                return field_value
            try:
                injected_kw = _inject_lazy_load(cached_field_type, field_value, use_lazy)
                return cached_field_type(**injected_kw)
            except Exception:
                return field_value

        if isinstance(field_type, str):
            # Convert Unions if any
            field_type = _pep604_to_union(field_type)
            try:
                field_type = eval(field_type)
            except Exception as e:
                return field_value

        field_type_origin = get_origin(field_type)
        if any(field_type_origin is scalar for scalar in SCALAR_TYPES):
            # Cache None for scalars
            _CACHED_TYPES[passed_field_type] = None
            return field_value

        # Check if the field type is a dataclass and if kwargs value is a dict
        if isinstance(field_type, type) and is_dataclass(field_type) and isinstance(field_value, dict):
            _CACHED_TYPES[passed_field_type] = field_type
            injected_kw = _inject_lazy_load(field_type, field_value, use_lazy)
            return field_type(**injected_kw)

        # Handle lists of dataclasses
        elif field_type_origin is list:
            value_type = get_args(field_type)
            if not value_type:
                return field_value
            list_arg_type = value_type[0]
            if is_dataclass(list_arg_type) and isinstance(field_value, list):
                return [
                    list_arg_type(**_inject_lazy_load(list_arg_type, item, use_lazy)) if isinstance(item, dict)
                    else item for item in field_value
                ]

            # Cache untyped general list
            _CACHED_TYPES[passed_field_type] = None
            return field_value

        # Handle Dict[SomeType, SomeDataClass]
        elif field_type_origin is dict:
            dict_kv_types = get_args(field_type)
            if not dict_kv_types:
                return field_value
            key_type, value_type = dict_kv_types
            if is_dataclass(value_type) and isinstance(field_value, dict):
                return {
                    key: value_type(**_inject_lazy_load(value_type, val, use_lazy)) if isinstance(val, dict)
                    else val for key, val in field_value.items()
                }

            # Cache untyped general dict
            _CACHED_TYPES[passed_field_type] = None
            return field_value

        # Handle everything else including Optional types
        else:
            real_type, field_type_is_optional = _extract_real_type(field_type)
            for item in real_type:
                try:
                    if any(item is scalar for scalar in SCALAR_TYPES):
                        # Cache None for scalars
                        res = field_value
                        _CACHED_TYPES[passed_field_type] = None
                    else:
                        injected_kw = _inject_lazy_load(item, field_value, use_lazy)
                        res = item(**injected_kw)
                        _CACHED_TYPES[passed_field_type] = item
                    return res
                except Exception as e:
                    pass

            # ToDo: Actually, we must do further checks for origin of each real_type here, not just take the first one
            # If we were not able to find any type, just go with the first one
            real_type = real_type[0]

            # If we have complex type still, decompose it further
            if "[" in str(real_type):
                return _evaluate_value(real_type, field_value, use_lazy)

            if isinstance(real_type, type):
                try:
                    injected_kw = _inject_lazy_load(real_type, field_value, use_lazy)
                    res = real_type(**injected_kw)
                    _CACHED_TYPES[passed_field_type] = real_type
                    return res
                except (SyntaxError, NameError, TypeError):
                    pass

            try:
                real_type_origin = get_origin(real_type)
                if any(real_type_origin is scalar for scalar in SCALAR_TYPES):
                    # Cache None for scalars
                    res = field_value
                    _CACHED_TYPES[passed_field_type] = None
                else:
                    injected_kw = _inject_lazy_load(real_type_origin, field_value, use_lazy)
                    res = real_type_origin(**injected_kw)
                    _CACHED_TYPES[passed_field_type] = real_type_origin
                return res
            except (SyntaxError, NameError, TypeError):
                # Give up here. It could be enum or some other complex scalar, just leave it as is.
                _CACHED_TYPES[passed_field_type] = None
                return field_value
            except BaseException:
                raise
    except BaseException as e:
        logging.critical(
            f"Unexpected error in _evaluate_value for type {field_type} and value {field_value}. Error: {e}")
        raise


def k8s_timestamp_field(**kwargs) -> Field:
    return field(**kwargs, metadata={
        "decoder": lambda iso_date: datetime.fromisoformat(iso_date) if type(iso_date) is str else iso_date,
        "encoder": lambda date: date.isoformat("T", "seconds").replace("+00:00", "Z")
        if type(date) is datetime else date
    })


def _to_immutable(v: Any) -> Any:
    """Best-effort stable, hashable transform for nested structures."""
    try:
        hash(v)
        return v
    except TypeError:
        if isinstance(v, dict):
            return tuple(sorted((k, _to_immutable(val)) for k, val in v.items()))
        if isinstance(v, (list, tuple)):
            return tuple(_to_immutable(x) for x in v)
        if isinstance(v, set):
            return tuple(sorted(_to_immutable(x) for x in v))
        # Fallback for user types
        return repr(v)


class _LazyLoadMeta(type):
    """
    A metaclass for dataclasses to automatically decode fields during initialization
    based on the metadata configuration and typing.

    This metaclass intercepts the initialization arguments and applies the specified
    decoder functions (if any) from the field metadata before passing the arguments
    to the original ``__init__`` method.
    """
    def __call__(cls, *args, **kwargs):
        new_kw: Dict[str, Any] = {}
        supports_lazy = issubclass(cls, LazyLoadModel)
        lazy_requested = kwargs.get(_LOAD_LAZY_FIELD)
        should_use_lazy = supports_lazy and lazy_requested
        
        if not getattr(cls, "__lazy_methods_patched", False):
            cls.__eq__ = LazyLoadModel.__eq__
            cls.__hash__ = LazyLoadModel.__hash__
            cls.__lazy_methods_patched = True

        if not should_use_lazy:
            if not kwargs.get(_LOAD_TYPES_ON_INIT):
                return super().__call__(*args, **kwargs)

            all_fields = fields(cls)
            type_hints = get_type_hints(cls)
            for f in all_fields:
                if not f.init or f.name not in kwargs:
                    continue

                # metadata-based decoder
                if "decoder" in f.metadata:
                    try:
                        kwargs[f.name] = _evaluate_field_by_meta(kwargs[f.name], f.metadata)
                        continue
                    except ValueError as e:
                        logging.error(
                            f"Got invalid decoder for {f.name} in class {cls.__name__}. "
                            f"Value was passed as is. Error: {e}"
                        )

                field_type = type_hints.get(f.name, f.type)
                if any(field_type is scalar for scalar in SCALAR_TYPES):
                    continue
                kwargs[f.name] = _evaluate_value(field_type, kwargs[f.name], use_lazy=False)

            new_kw = {
                k: v
                for k, v in kwargs.items()
                if k not in (_LOAD_LAZY_FIELD, _LOAD_TYPES_ON_INIT)
            }
        else:
            # Lazy path: store original kwargs as _lazy_src, set list/dict fields to None
            for k, v in kwargs.items():
                if k == _LOAD_TYPES_ON_INIT:
                    continue
                new_kw[k] = v if type(v) not in [list, dict] else None

            new_kw |= {_LAZY_SRC_FIELD: kwargs, _LOAD_LAZY_FIELD: True}

        return super().__call__(*args, **new_kw)


@dataclass(slots=True, kw_only=True, frozen=True)
class LazyLoadModel(metaclass=_LazyLoadMeta):
    """
    This model supports lazy loading of complex nested types avoiding unnecessary heavy recursions.
    """
    _lazy_src: Dict = field(default_factory=dict)
    _lazy: bool = field(default=False)

    @classmethod
    def from_dict(cls, src: dict[str, Any], lazy: bool = True) -> Self:
        params = dict(src)
        params[_LOAD_LAZY_FIELD] = lazy
        params[_LOAD_TYPES_ON_INIT] = True
        return cls(**params)

    def __getattribute__(self, name: str) -> Any:
        """
        Overrides the default attribute access behavior to implement lazy construction of Python types in a runtime.
        This method ensures that all the fields are only computed when they are accessed for the first time.

        If these attributes are not set during initialization (i.e., they are None),
        this method triggers their class constructor. This approach allows the class to behave like a normal
        frozen dataclass, where attributes can be accessed directly without the user needing to be aware
        of the lazy loading mechanism.
        """

        # Lazy load release data from body, if it's not set yet. We're interested in the following fields only.
        if name in [_LAZY_SRC_FIELD, _LOAD_LAZY_FIELD] or name.startswith("__") \
                or name not in getattr(self.__class__, "__dataclass_fields__", {}):
            # Never call super() here:
            # slotted dataclass have no __class__ attribute, which will end with infinite recursion
            return object.__getattribute__(self, name)

        # Check if we even want lazy load for this instance
        if not self._lazy:
            return object.__getattribute__(self, name)

        current_value = object.__getattribute__(self, name)
        if current_value is not None:
            return current_value
        src_value = self._lazy_src.get(name)
        if current_value == src_value:
            return current_value

        field_value, decoded = None, False
        f = self.__class__.__dataclass_fields__[name]
        if "decoder" in f.metadata:
            try:
                field_value = _evaluate_field_by_meta(src_value, f.metadata)
                decoded = True
            except ValueError as e:
                logging.error(f"Got invalid decoder for {f.name} in class {self.__class__.__name__}. "
                              f"Value was passed as is. Error: {e}")
                pass

        if not decoded:
            globalns = vars(sys.modules[self.__module__])
            hints = get_type_hints(self.__class__, globalns=globalns, include_extras=True)
            field_type = hints[name]
            field_value = _evaluate_value(field_type, src_value, use_lazy=self._lazy)
        object.__setattr__(self, name, field_value)
        return object.__getattribute__(self, name)

    def _realize_all(self) -> None:
        """Force load of all dataclass fields"""
        for n in getattr(self.__class__, "__dataclass_fields__", {}):
            if n in [_LAZY_SRC_FIELD, _LOAD_LAZY_FIELD]:
                continue
            _ = getattr(self, n)

    def __getstate__(self):
        """
        Make deepcopy/pickle see realized state without implementing our own deepcopy.
        """
        self._realize_all()

        # build state dict manually (slots instance has no __dict__)
        cls = object.__getattribute__(self, "__class__")
        state = {}
        for f in fields(cls):
            state[f.name] = object.__getattribute__(self, f.name)
        return state

    def __setstate__(self, state):
        # Restore state (allowed for frozen via object.__setattr__)
        for k, v in state.items():
            object.__setattr__(self, k, v)

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        """
        Align hash with equality: hash over realized public fields only.
        Avoids mismatch where two objects are == but have different hashes.
        """
        self._realize_all()
        public_items = []
        for n in getattr(self.__class__, "__dataclass_fields__", {}):
            if n in [_LAZY_SRC_FIELD, _LOAD_LAZY_FIELD]:
                continue
            public_items.append((n, _to_immutable(getattr(self, n))))
        return hash(tuple(public_items))

    def to_dict(self) -> dict[str, Any]:
        result = {}
        for f in fields(self):
            # Skip all private fields
            if f.name.startswith("_"):
                continue

            # Skip dataclass fields with the 'exclude_from_dict' flag in metadata
            if f.metadata.get(EXCLUDE_FIELD_META_KEY):
                continue

            value = getattr(self, f.name)
            encoder = f.metadata.get('encoder')
            value = encoder(value) if encoder else value

            # Recursively call to_dict if the field is a dataclass instance
            if is_dataclass(value):
                value = value.to_dict()
            elif isinstance(value, list) and all(is_dataclass(item) for item in value):
                value = [item.to_dict() for item in value]
            elif isinstance(value, dict):
                new_value = {}
                for k, v in value.items():
                    new_value[k] = v.to_dict() if is_dataclass(v) else v
                value = new_value

            result[f.name] = value
        return result
