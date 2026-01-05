from __future__ import annotations

import logging

import enum
import types
from dataclasses import MISSING, dataclass, field, fields, is_dataclass, replace
from typing import Any, ClassVar, get_args, get_origin, get_type_hints, Callable, Union, Annotated

from kube_models import K8sResource, Loadable
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kube_models.apis_apiextensions_k8s_io_v1.io.k8s.apiextensions_apiserver.pkg.apis.apiextensions.v1 import (
    CustomResourceDefinition,
    CustomResourceDefinitionSpec,
    CustomResourceDefinitionNames,
    CustomResourceDefinitionVersion,
    CustomResourceColumnDefinition,
    CustomResourceValidation,
    JSONSchemaProps,
    CustomResourceSubresources
)


@dataclass(slots=True, kw_only=True, frozen=True)
class PrinterColumn(CustomResourceColumnDefinition):
    # Override fields with empty defaults, so they can be autofilled by the generator
    jsonPath: str = field(init=False, default="")
    type: str = field(init=False, default="")
    name: str = ""


@dataclass(slots=True, kw_only=True, frozen=True)
class CustomK8sResource(K8sResource):
    """
    One class = one CRD version. Multiple versions -> multiple classes.
    """
    _served: ClassVar[bool] = True
    _storage: ClassVar[bool] = False
    _deprecated: ClassVar[bool | None] = None
    _deprecation_warning: ClassVar[str | None] = None
    _subresources: ClassVar[CustomResourceSubresources | None] = None


@dataclass
class CustomK8sResourceDefinition:
    versions: ClassVar[list[type[CustomK8sResource]]]

    crd_short_names_: ClassVar[list[str] | None] = None
    crd_categories_: ClassVar[list[str] | None] = None
    crd_singular_: ClassVar[str | None] = None
    crd_list_kind_: ClassVar[str | None] = None
    crd_preserve_unknown_fields_: ClassVar[bool | None] = None

    def build(self) -> CustomResourceDefinition:
        versions: list[type[K8sResource]] = self.versions
        for version_class in versions:
            if not issubclass(version_class, CustomK8sResource):
                raise TypeError(f"{version_class!r} is not a CustomK8sResource subclass")

        first_class = versions[0]
        group = first_class.group_
        kind = first_class.kind
        plural = first_class.plural_
        scope = "Namespaced" if first_class.is_namespaced_ else "Cluster"

        # Validate consistency among multiple versions
        for version_class in versions:
            msg = None
            if version_class.group_ != group:
                msg = f"group mismatch: {first_class.__name__}={group} vs {version_class.__name__}={version_class.group_}"
            elif version_class.kind != kind:
                msg = f"kind mismatch: {first_class.__name__}={kind} vs {version_class.__name__}={version_class.kind}"
            elif version_class.plural_ != plural:
                msg = f"plural_ mismatch: {first_class.__name__}={plural} vs {version_class.__name__}={version_class.plural_}"
            else:
                current_scope = "Namespaced" if version_class.is_namespaced_ else "Cluster"
                if current_scope != scope:
                    msg = f"scope mismatch: {first_class.__name__}={scope} vs {version_class.__name__}={current_scope}"
            if msg:
                raise ValueError(msg)

        crd_names = CustomResourceDefinitionNames(
            kind=kind,
            plural=plural,
            shortNames=self.crd_short_names_,
            categories=self.crd_categories_,
            singular=self.crd_singular_,
            listKind=self.crd_list_kind_
        )

        # Pick storage version (default: last)
        _storageindices = [index for index, version_class in enumerate(versions) if version_class._storage]
        if len(_storageindices) == 0:
            _storageversion_index = len(versions) - 1
        elif len(_storageindices) == 1:
            _storageversion_index = _storageindices[0]
        else:
            raise ValueError("Multiple version classes set _storage=True; exactly one must be storage")

        # Ensure version names are unique (e.g. v1alpha1, v1beta1)
        seen_versions: set[str] = set()
        for version_class in versions:
            version_name = version_class.apiVersion.split("/", 1)[-1]
            if version_name in seen_versions:
                raise ValueError(f"Duplicate version name '{version_name}' in {self.__class__.__name__}.versions")
            seen_versions.add(version_name)

        crd_versions: list[CustomResourceDefinitionVersion] = []
        for index, version_class in enumerate(versions):
            version_name = version_class.apiVersion.split("/", 1)[-1]

            schema_root = _resource_schema(version_class)
            printer_columns = _collect_printer_columns(version_class)

            subresources = version_class._subresources
            crd_versions.append(
                CustomResourceDefinitionVersion(
                    name=version_name,
                    served=version_class._served,
                    storage=index == _storageversion_index,
                    deprecated=version_class._deprecated,
                    deprecationWarning=version_class._deprecation_warning,
                    additionalPrinterColumns=printer_columns or None,
                    subresources=subresources,
                    schema_=CustomResourceValidation(openAPIV3Schema=schema_root)
                )
            )

        metadata = ObjectMeta(name=f"{plural}.{group}")
        spec = CustomResourceDefinitionSpec(
            group=group,
            names=crd_names,
            scope=scope,
            versions=crd_versions,
            preserveUnknownFields=self.crd_preserve_unknown_fields_
        )
        return CustomResourceDefinition(metadata=metadata, spec=spec)


@dataclass(slots=True, kw_only=True, frozen=True)
class CRDFieldSpec(JSONSchemaProps):
    """
    Anything you set here **will override** the auto-generated schema for that field (only for non-None values).
    """
    printer_column: PrinterColumn | None = None


def _get_crd_field_spec(f) -> CRDFieldSpec | None:
    spec = f.metadata.get("kubesdk_crd_field_spec")
    return spec if isinstance(spec, CRDFieldSpec) else None


def crd_field(*, default: Any = MISSING, default_factory: Callable = MISSING, spec: CRDFieldSpec | None = None):
    """
    Attach CRD-related metadata to any CustomK8sResource' or nested Loadable dataclass' field.

    Example:
        >>> class MyCustomResource(CustomK8sResource): \
                enabled: bool = crd_field(spec=CRDFieldSpec(printer_column=PrinterColumn(name="Enabled")))
    """
    metadata: dict[str, Any] = {}
    if spec is not None:
        metadata["kubesdk_crd_field_spec"] = spec

    field_kwargs: dict[str, Any] = {}
    if metadata:
        field_kwargs["metadata"] = metadata
    if default is not MISSING:
        field_kwargs["default"] = default
    if default_factory is not MISSING:
        field_kwargs["default_factory"] = default_factory

    return field(**field_kwargs)


def _apply_crd_field_overrides(base: JSONSchemaProps, spec: CRDFieldSpec) -> JSONSchemaProps:
    """
    Merge CRDFieldSpec into an auto-generated JSONSchemaProps.
    Only non-None values in CRDFieldSpec are applied.
    """
    overrides: dict[str, Any] = {}
    for f in fields(spec):
        # Skip non-schema props
        if f.name == "printer_column" or f.name.startswith("_"):
            continue
        v = getattr(spec, f.name)
        if v is not None:
            overrides[f.name] = v
    return replace(base, **overrides) if overrides else base


def _is_jsonpath_identifier(token: str) -> bool:
    if not token:
        return False
    c0 = token[0]
    if not (c0.isalpha() or c0 == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in token[1:])


def _kubectl_jsonpath_from_segments(segments: list[str]) -> str:
    """
    As per https://kubernetes.io/docs/reference/kubectl/jsonpath/
    kubectl JSONPath:
      - str identifier -> .name
      - str non-identifier -> ['raw-token'] (no escaping by design)
      - int -> [N]
      - "*" -> [*]
    """
    if not segments:
        return "."

    out = ""
    for seg in segments:
        if isinstance(seg, int):
            out += f"[{seg}]"
        elif seg == "*":
            out += "[*]"
        elif _is_jsonpath_identifier(seg):
            out += "." + seg
        else:
            out += "['" + seg + "']"
    return out or "."


def _collect_printer_columns(resource_class: type) -> list[CustomResourceColumnDefinition]:
    if not is_dataclass(resource_class):
        raise TypeError("resource_class must be a dataclass")

    type_hints = get_type_hints(resource_class, include_extras=True)
    spec_type = type_hints.get("spec", Any)
    status_type = type_hints.get("status", None)

    cols: list[CustomResourceColumnDefinition] = []
    cols.extend(_collect_printer_columns_from_dataclass(spec_type, prefix_segments=["spec"]))

    if status_type is not None:
        cols.extend(_collect_printer_columns_from_dataclass(status_type, prefix_segments=["status"]))

    return cols


def _collect_printer_columns_from_dataclass(dataclass_type: type[Loadable], *, prefix_segments: list[str]) \
        -> list[CustomResourceColumnDefinition]:
    dataclass_type = _strip_annotated(dataclass_type)
    if _is_optional_type(dataclass_type):
        dataclass_type = _optional_inner_type(dataclass_type)

    type_hints = get_type_hints(dataclass_type, include_extras=True)
    out: list[CustomResourceColumnDefinition] = []

    # noinspection PyDataclass
    for f in fields(dataclass_type):
        field_type = type_hints.get(f.name, Any)

        segs_here: list[str] = [*prefix_segments, f.name]
        default_path = _kubectl_jsonpath_from_segments(segs_here)

        crd_spec = _get_crd_field_spec(f)
        col = crd_spec.printer_column if (crd_spec is not None) else None
        if col is not None:
            out.append(
                CustomResourceColumnDefinition(
                    jsonPath=(col.jsonPath or default_path),
                    name=(col.name or f.name),
                    type=(col.type or _default_column_type(field_type)),
                    description=col.description,
                    format=col.format,
                    priority=col.priority,
                )
            )
        out.extend(_recurse_printer_columns(field_type, segs_here))

    return out


def _recurse_printer_columns(field_type: Any, segs_here: list[str]) -> list[CustomResourceColumnDefinition]:
    ft = _strip_annotated(field_type)
    if _is_optional_type(ft):
        ft = _optional_inner_type(ft)

    origin = get_origin(ft)

    # list[T] / tuple[T, ...] -> recurse into T with [*]
    if origin in (list, tuple):
        args = get_args(ft)
        if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            item_type = args[0]
        else:
            item_type = args[0] if args else Any

        item_type = _strip_annotated(item_type)
        if _is_optional_type(item_type):
            item_type = _optional_inner_type(item_type)

        if is_dataclass(item_type):
            return _collect_printer_columns_from_dataclass(item_type, prefix_segments=[*segs_here, "*"])
        return []

    # direct nested dataclass
    if is_dataclass(ft):
        return _collect_printer_columns_from_dataclass(ft, prefix_segments=segs_here)

    return []


def _default_column_type(python_type: Any) -> str:
    python_type = _strip_annotated(python_type)
    if _is_optional_type(python_type):
        python_type = _optional_inner_type(python_type)

    origin = get_origin(python_type)
    if origin in (list, tuple, dict, set, frozenset):
        return "string"

    if python_type is bool:
        return "boolean"
    if python_type is int:
        return "integer"
    if python_type is float:
        return "number"
    if python_type is str:
        return "string"
    if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
        return "string"
    return "string"


def _resource_schema(resource_class: type[K8sResource]) -> JSONSchemaProps:
    if not is_dataclass(resource_class):
        raise TypeError("resource_class must be a dataclass")

    type_hints = get_type_hints(resource_class, include_extras=True)

    properties: dict[str, JSONSchemaProps] = {
        "apiVersion": JSONSchemaProps(type="string"),
        "kind": JSONSchemaProps(type="string")
    }

    for f in fields(resource_class):
        if f.name.startswith("_") or f.name == "metadata":
            continue
        field_type = type_hints.get(f.name, Any)
        schema = _schema_from_type(field_type)
        crd_spec = _get_crd_field_spec(f)
        if crd_spec is not None:
            schema = _apply_crd_field_overrides(schema, crd_spec)

        properties[f.name] = schema

    return JSONSchemaProps(type="object", properties=properties, required=None)


def _schema_from_type(python_type: Any) -> JSONSchemaProps:
    python_type = _strip_annotated(python_type)

    if _is_optional_type(python_type):
        inner_schema = _schema_from_type(_optional_inner_type(python_type))
        return replace(inner_schema, nullable=True)

    origin = get_origin(python_type)

    if origin is list:
        args = get_args(python_type)
        item_type = args[0] if args else Any
        return JSONSchemaProps(type="array", items=_schema_from_type(item_type))

    if origin is tuple:
        args = get_args(python_type)
        # tuple[T, ...] -> array of T
        if len(args) == 2 and args[1] is Ellipsis:
            return JSONSchemaProps(type="array", items=_schema_from_type(args[0]))
        # fixed-length tuple -> treat as array of Any
        return JSONSchemaProps(type="array", items=_schema_from_type(Any))

    if origin is dict:
        args = get_args(python_type)
        value_type = args[1] if len(args) >= 2 else Any
        return JSONSchemaProps(type="object", additionalProperties=_schema_from_type(value_type))

    if python_type is str:
        return JSONSchemaProps(type="string")
    if python_type is int:
        return JSONSchemaProps(type="integer")
    if python_type is float:
        return JSONSchemaProps(type="number")
    if python_type is bool:
        return JSONSchemaProps(type="boolean")
    if python_type is Any:
        return JSONSchemaProps(type="object", x_kubernetes_preserve_unknown_fields=True)

    if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
        return JSONSchemaProps(type="string", enum=[member.value for member in python_type])

    if is_dataclass(python_type):
        return _schema_from_dataclass_type(python_type)

    # Fallback to broad object
    logging.warning(f"Got field of {python_type} type. Field type was ignored and processed as unknown object.")
    return JSONSchemaProps(type="object", x_kubernetes_preserve_unknown_fields=True)


def _schema_from_dataclass_type(dataclass_type: type[Loadable]) -> JSONSchemaProps:
    type_hints = get_type_hints(dataclass_type, include_extras=True)
    properties: dict[str, JSONSchemaProps] = {}
    required_fields: list[str] = []

    # noinspection PyDataclass
    for f in fields(dataclass_type):
        if f.name.startswith("_"):
            continue

        field_type = type_hints.get(f.name, Any)
        schema = _schema_from_type(field_type)
        crd_spec = _get_crd_field_spec(f)
        if crd_spec is not None:
            schema = _apply_crd_field_overrides(schema, crd_spec)

        properties[f.name] = schema

        has_default = (f.default is not MISSING) or (f.default_factory is not MISSING)
        is_optional = _is_optional_type(_strip_annotated(field_type))
        if (not has_default) and (not is_optional):
            required_fields.append(f.name)

    return JSONSchemaProps(type="object", properties=properties, required=required_fields or None)


#
# Type helpers
#
def _strip_annotated(python_type: Any) -> Any:
    if get_origin(python_type) is Annotated:
        return get_args(python_type)[0]
    return python_type


def _is_optional_type(python_type: Any) -> bool:
    origin = get_origin(python_type)
    return origin in (types.UnionType, Union) and any(arg is type(None) for arg in get_args(python_type))


def _optional_inner_type(python_type: Any) -> Any:
    args = [arg for arg in get_args(python_type) if arg is not type(None)]
    return args[0] if len(args) == 1 else Any
