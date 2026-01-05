from __future__ import annotations

import builtins
import contextlib
import copy
import logging
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, Final, TextIO, TypeVar, cast, Dict, ClassVar, List, Generic
from urllib.parse import ParseResult

import yaml
import yaml.parser

import datamodel_code_generator.pydantic_patch  # noqa: F401
from datamodel_code_generator.format import (
    DEFAULT_FORMATTERS,
    DatetimeClassType,
    Formatter,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator.types import Types
from datamodel_code_generator.parser import DefaultPutDict, LiteralType
from datamodel_code_generator.model.base import BaseClassDataType
from datamodel_code_generator.model.pydantic_v2 import UnionMode
from datamodel_code_generator.parser.base import Parser
from datamodel_code_generator.util import SafeLoader

from datamodel_code_generator.parser.openapi import *
from datamodel_code_generator import DataModelType, GraphQLScope, get_first_file, RAW_DATA_TYPES, \
    get_version, chdir, MAX_VERSION, MIN_VERSION, InvalidClassNameError, is_openapi, is_schema
from datamodel_code_generator.model.imports import IMPORT_CLASSVAR

from kubesdk_cli.templates.const import *
from kubesdk_cli.const import *


NON_SCALAR_TYPES: list[type] = [list, dict, tuple, set]


class EmptyComponents(Exception): ...


_T = TypeVar("_T")


class _SortedSet(builtins.set, Generic[_T]):
    """Mocked set to have deterministic ordered output"""
    def __iter__(self): return iter(sorted(builtins.set.__iter__(self)))

    def __repr__(self):
        # deterministic, set-like repr
        if not self:
            return "set()"
        return "{" + ", ".join(repr(x) for x in self) + "}"


class InputFileType(Enum):
    Auto = "auto"
    OpenAPI = "openapi"
    OpenAPIK8s = "k8s"
    JsonSchema = "jsonschema"
    Json = "json"
    Yaml = "yaml"
    Dict = "dict"
    CSV = "csv"
    GraphQL = "graphql"


def generate(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
    input_: Path | str | ParseResult | Mapping[str, Any],
    *,
    input_filename: str | None = None,
    input_file_type: InputFileType = InputFileType.Auto,
    output: Path | None = None,
    output_model_type: DataModelType = DataModelType.PydanticBaseModel,
    target_python_version: PythonVersion = PythonVersionMin,
    base_class: str = "",
    additional_imports: list[str] | None = None,
    custom_template_dir: Path | None = None,
    extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
    validation: bool = False,
    field_constraints: bool = False,
    snake_case_field: bool = False,
    strip_default_none: bool = False,
    aliases: Mapping[str, str] | None = None,
    disable_timestamp: bool = True,
    enable_version_header: bool = False,
    allow_population_by_field_name: bool = False,
    allow_extra_fields: bool = False,
    extra_fields: str | None = None,
    apply_default_values_for_required_fields: bool = False,
    force_optional_for_required_fields: bool = False,
    class_name: str | None = None,
    use_standard_collections: bool = False,
    use_schema_description: bool = False,
    use_field_description: bool = False,
    use_default_kwarg: bool = False,
    reuse_model: bool = False,
    encoding: str = "utf-8",
    enum_field_as_literal: LiteralType | None = None,
    use_one_literal_as_default: bool = False,
    set_default_enum_member: bool = False,
    use_subclass_enum: bool = False,
    strict_nullable: bool = False,
    use_generic_container_types: bool = False,
    enable_faux_immutability: bool = False,
    disable_appending_item_suffix: bool = False,
    strict_types: Sequence[StrictTypes] | None = None,
    empty_enum_field_name: str | None = None,
    custom_class_name_generator: Callable[[str], str] | None = None,
    field_extra_keys: set[str] | None = None,
    field_include_all_keys: bool = False,
    field_extra_keys_without_x_prefix: set[str] | None = None,
    openapi_scopes: list[OpenAPIScope] | None = None,
    include_path_parameters: bool = False,
    graphql_scopes: list[GraphQLScope] | None = None,  # noqa: ARG001
    wrap_string_literal: bool | None = None,
    use_title_as_name: bool = False,
    use_operation_id_as_name: bool = False,
    use_unique_items_as_set: bool = False,
    http_headers: Sequence[tuple[str, str]] | None = None,
    http_ignore_tls: bool = False,
    use_annotated: bool = False,
    use_non_positive_negative_number_constrained_types: bool = False,
    original_field_name_delimiter: str | None = None,
    use_double_quotes: bool = False,
    use_union_operator: bool = False,
    collapse_root_models: bool = False,
    special_field_name_prefix: str | None = None,
    remove_special_field_name_prefix: bool = False,
    capitalise_enum_members: bool = False,
    keep_model_order: bool = False,
    custom_file_header: str | None = None,
    custom_file_header_path: Path | None = None,
    custom_formatters: list[str] | None = None,
    custom_formatters_kwargs: dict[str, Any] | None = None,
    use_pendulum: bool = False,
    http_query_parameters: Sequence[tuple[str, str]] | None = None,
    treat_dot_as_module: bool = False,
    use_exact_imports: bool = False,
    union_mode: UnionMode | None = None,
    output_datetime_class: DatetimeClassType | None = None,
    keyword_only: bool = False,
    frozen_dataclasses: bool = False,
    no_alias: bool = False,
    formatters: list[Formatter] = DEFAULT_FORMATTERS,
    parent_scoped_naming: bool = False,
) -> None:
    remote_text_cache: DefaultPutDict[str, str] = DefaultPutDict()
    if isinstance(input_, str):
        input_text: str | None = input_
    elif isinstance(input_, ParseResult):
        from datamodel_code_generator.http import get_body  # noqa: PLC0415

        input_text = remote_text_cache.get_or_put(
            input_.geturl(),
            default_factory=lambda url: get_body(url, http_headers, http_ignore_tls, http_query_parameters),
        )
    else:
        input_text = None

    if isinstance(input_, Path) and not input_.is_absolute():
        input_ = input_.expanduser().resolve()
    if input_file_type == InputFileType.Auto:
        try:
            input_text_ = (
                get_first_file(input_).read_text(encoding=encoding) if isinstance(input_, Path) else input_text
            )
        except FileNotFoundError as exc:
            msg = "File not found"
            raise Error(msg) from exc

        try:
            assert isinstance(input_text_, str)
            input_file_type = infer_input_type(input_text_)
        except Exception as exc:
            msg = "Invalid file format"
            raise Error(msg) from exc
        else:
            print(  # noqa: T201
                inferred_message.format(input_file_type.value),
                file=sys.stderr,
            )

    kwargs: dict[str, Any] = {}
    if input_file_type == InputFileType.OpenAPI:  # noqa: PLR1702
        from datamodel_code_generator.parser.openapi import OpenAPIParser  # noqa: PLC0415

        parser_class: type[Parser] = OpenAPIParser
        kwargs["openapi_scopes"] = openapi_scopes
        kwargs["include_path_parameters"] = include_path_parameters

    # Our own parser here
    elif input_file_type == InputFileType.OpenAPIK8s:
        parser_class: type[Parser] = OpenAPIK8sParser
        kwargs["openapi_scopes"] = openapi_scopes
        kwargs["include_path_parameters"] = include_path_parameters

    elif input_file_type == InputFileType.GraphQL:
        from datamodel_code_generator.parser.graphql import GraphQLParser  # noqa: PLC0415

        parser_class: type[Parser] = GraphQLParser

    else:
        from datamodel_code_generator.parser.jsonschema import JsonSchemaParser  # noqa: PLC0415

        parser_class = JsonSchemaParser

        if input_file_type in RAW_DATA_TYPES:
            import json  # noqa: PLC0415

            try:
                if isinstance(input_, Path) and input_.is_dir():  # pragma: no cover
                    msg = f"Input must be a file for {input_file_type}"
                    raise Error(msg)  # noqa: TRY301
                obj: dict[Any, Any]
                if input_file_type == InputFileType.CSV:
                    import csv  # noqa: PLC0415

                    def get_header_and_first_line(csv_file: IO[str]) -> dict[str, Any]:
                        csv_reader = csv.DictReader(csv_file)
                        assert csv_reader.fieldnames is not None
                        return dict(zip(csv_reader.fieldnames, next(csv_reader)))

                    if isinstance(input_, Path):
                        with input_.open(encoding=encoding) as f:
                            obj = get_header_and_first_line(f)
                    else:
                        import io  # noqa: PLC0415

                        obj = get_header_and_first_line(io.StringIO(input_text))
                elif input_file_type == InputFileType.Yaml:
                    if isinstance(input_, Path):
                        obj = load_yaml(input_.read_text(encoding=encoding))
                    else:
                        assert input_text is not None
                        obj = load_yaml(input_text)
                elif input_file_type == InputFileType.Json:
                    if isinstance(input_, Path):
                        obj = json.loads(input_.read_text(encoding=encoding))
                    else:
                        assert input_text is not None
                        obj = json.loads(input_text)
                elif input_file_type == InputFileType.Dict:
                    import ast  # noqa: PLC0415

                    # Input can be a dict object stored in a python file
                    obj = (
                        ast.literal_eval(input_.read_text(encoding=encoding))
                        if isinstance(input_, Path)
                        else cast("dict[Any, Any]", input_)
                    )
                else:  # pragma: no cover
                    msg = f"Unsupported input file type: {input_file_type}"
                    raise Error(msg)  # noqa: TRY301
            except Exception as exc:
                msg = "Invalid file format"
                raise Error(msg) from exc

            from genson import SchemaBuilder  # noqa: PLC0415

            builder = SchemaBuilder()
            builder.add_object(obj)
            input_text = json.dumps(builder.to_schema())

    if isinstance(input_, ParseResult) and input_file_type not in RAW_DATA_TYPES:
        input_text = None

    if union_mode is not None:
        if output_model_type == DataModelType.PydanticV2BaseModel:
            default_field_extras = {"union_mode": union_mode}
        else:  # pragma: no cover
            msg = "union_mode is only supported for pydantic_v2.BaseModel"
            raise Error(msg)
    else:
        default_field_extras = None

    from datamodel_code_generator.model import get_data_model_types  # noqa: PLC0415

    data_model_types = get_data_model_types(output_model_type, target_python_version)
    source = input_text or input_
    assert not isinstance(source, Mapping)
    parser = parser_class(
        source=source,
        data_model_type=data_model_types.data_model,
        data_model_root_type=data_model_types.root_model,
        data_model_field_type=data_model_types.field_model,
        data_type_manager_type=data_model_types.data_type_manager,
        base_class=base_class,
        additional_imports=additional_imports,
        custom_template_dir=custom_template_dir,
        extra_template_data=extra_template_data,
        target_python_version=target_python_version,
        dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
        validation=validation,
        field_constraints=field_constraints,
        snake_case_field=snake_case_field,
        strip_default_none=strip_default_none,
        aliases=aliases,
        allow_population_by_field_name=allow_population_by_field_name,
        allow_extra_fields=allow_extra_fields,
        extra_fields=extra_fields,
        apply_default_values_for_required_fields=apply_default_values_for_required_fields,
        force_optional_for_required_fields=force_optional_for_required_fields,
        class_name=class_name,
        use_standard_collections=use_standard_collections,
        base_path=input_.parent if isinstance(input_, Path) and input_.is_file() else None,
        use_schema_description=use_schema_description,
        use_field_description=use_field_description,
        use_default_kwarg=use_default_kwarg,
        reuse_model=reuse_model,
        enum_field_as_literal=LiteralType.All
        if output_model_type == DataModelType.TypingTypedDict
        else enum_field_as_literal,
        use_one_literal_as_default=use_one_literal_as_default,
        set_default_enum_member=True
        if output_model_type == DataModelType.DataclassesDataclass
        else set_default_enum_member,
        use_subclass_enum=use_subclass_enum,
        strict_nullable=strict_nullable,
        use_generic_container_types=use_generic_container_types,
        enable_faux_immutability=enable_faux_immutability,
        remote_text_cache=remote_text_cache,
        disable_appending_item_suffix=disable_appending_item_suffix,
        strict_types=strict_types,
        empty_enum_field_name=empty_enum_field_name,
        custom_class_name_generator=custom_class_name_generator,
        field_extra_keys=field_extra_keys,
        field_include_all_keys=field_include_all_keys,
        field_extra_keys_without_x_prefix=field_extra_keys_without_x_prefix,
        wrap_string_literal=wrap_string_literal,
        use_title_as_name=use_title_as_name,
        use_operation_id_as_name=use_operation_id_as_name,
        use_unique_items_as_set=use_unique_items_as_set,
        http_headers=http_headers,
        http_ignore_tls=http_ignore_tls,
        use_annotated=use_annotated,
        use_non_positive_negative_number_constrained_types=use_non_positive_negative_number_constrained_types,
        original_field_name_delimiter=original_field_name_delimiter,
        use_double_quotes=use_double_quotes,
        use_union_operator=use_union_operator,
        collapse_root_models=collapse_root_models,
        special_field_name_prefix=special_field_name_prefix,
        remove_special_field_name_prefix=remove_special_field_name_prefix,
        capitalise_enum_members=capitalise_enum_members,
        keep_model_order=keep_model_order,
        known_third_party=data_model_types.known_third_party,
        custom_formatters=custom_formatters,
        custom_formatters_kwargs=custom_formatters_kwargs,
        use_pendulum=use_pendulum,
        http_query_parameters=http_query_parameters,
        treat_dot_as_module=treat_dot_as_module,
        use_exact_imports=use_exact_imports,
        default_field_extras=default_field_extras,
        target_datetime_class=output_datetime_class,
        keyword_only=keyword_only,
        frozen_dataclasses=frozen_dataclasses,
        no_alias=no_alias,
        formatters=formatters,
        encoding=encoding,
        parent_scoped_naming=parent_scoped_naming,
        **kwargs,
    )

    with chdir(output):
        results = parser.parse()
    if not input_filename:  # pragma: no cover
        if isinstance(input_, str):
            input_filename = "<stdin>"
        elif isinstance(input_, ParseResult):
            input_filename = input_.geturl()
        elif input_file_type == InputFileType.Dict:
            # input_ might be a dict object provided directly, and missing a name field
            input_filename = getattr(input_, "name", "<dict>")
        else:
            assert isinstance(input_, Path)
            input_filename = input_.name
    if not results:
        msg = "Models not found in the input data"
        raise Error(msg)
    if isinstance(results, str):
        modules = {output: (results, input_filename)}
    else:
        if output is None:
            msg = "Modular references require an output directory"
            raise Error(msg)
        if output.suffix:
            msg = "Modular references require an output directory, not a file"
            raise Error(msg)
        modules = {
            output.joinpath(*name): (
                result.body,
                str(result.source.as_posix() if result.source else input_filename),
            )
            for name, result in sorted(results.items())
        }

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if custom_file_header is None and custom_file_header_path:
        custom_file_header = custom_file_header_path.read_text(encoding=encoding)

    header = f"""{GENERATED_HEADER}#   filename:  {{}}"""
    if not disable_timestamp:
        header += f"\n#   timestamp: {timestamp}"
    if enable_version_header:
        header += f"\n#   version:   {get_version()}"

    file: IO[Any] | None
    for path, (body, filename) in modules.items():
        if path is None:
            file = None
        else:
            if not path.parent.exists():
                path.parent.mkdir(parents=True)
            file = path.open("wt", encoding=encoding)

        safe_filename = filename.replace("\n", " ").replace("\r", " ") if filename else ""
        print(custom_file_header or header.format(safe_filename), file=file)
        if body:
            print(file=file)
            print(body.rstrip(), file=file)

        if file is not None:
            file.close()


def infer_input_type(text: str) -> InputFileType:
    try:
        data = load_yaml(text)
    except yaml.parser.ParserError:
        return InputFileType.CSV
    if isinstance(data, dict):
        if is_openapi(data):
            return InputFileType.OpenAPI
        if is_schema(data):
            return InputFileType.JsonSchema
        return InputFileType.Json
    msg = (
        "Can't infer input file type from the input data. "
        "Please specify the input file type explicitly with --input-file-type option."
    )
    raise Error(msg)


inferred_message = (
    "The input file type was determined to be: {}\nThis can be specified explicitly with the "
    "`--input-file-type` option."
)

__all__ = [
    "MAX_VERSION",
    "MIN_VERSION",
    "DatetimeClassType",
    "DefaultPutDict",
    "Error",
    "InputFileType",
    "InvalidClassNameError",
    "LiteralType",
    "PythonVersion",
    "generate",
]


@dataclass(kw_only=True, frozen=True)
class OperationMeta:
    op: Operation
    path: str
    method: str


class OpenAPIK8sParser(OpenAPIParser):
    operations: List[OperationMeta] = []

    def parse_operation(self, raw_operation: dict[str, Any], path: list[str]) -> None:
        operation = Operation.parse_obj(raw_operation)
        path_name, method = path[-2:]
        try:
            if self.use_operation_id_as_name:
                if not operation.operationId:
                    msg = (
                        f"All operations must have an operationId when --use_operation_id_as_name is set."
                        f"The following path was missing an operationId: {path_name}"
                    )
                    raise Error(msg)
                path_name = operation.operationId
                method = ""
            self.parse_all_parameters(
                self._get_model_name(
                    path_name, method, suffix="Parameters" if self.include_path_parameters else "ParametersQuery"
                ),
                operation.parameters,
                [*path, "parameters"],
            )
            if operation.requestBody:
                if isinstance(operation.requestBody, ReferenceObject):
                    ref_model = self.get_ref_model(operation.requestBody.ref)
                    request_body = RequestBodyObject.parse_obj(ref_model)
                else:
                    request_body = operation.requestBody
                self.parse_request_body(
                    name=self._get_model_name(path_name, method, suffix="Request"),
                    request_body=request_body,
                    path=[*path, "requestBody"],
                )
            self.parse_responses(
                name=self._get_model_name(path_name, method, suffix="Response"),
                responses=operation.responses,
                path=[*path, "responses"],
            )
            if OpenAPIScope.Tags in self.open_api_scopes:
                self.parse_tags(
                    name=self._get_model_name(path_name, method, suffix="Tags"),
                    tags=operation.tags,
                    path=[*path, "tags"],
                )
        except Exception as e:
            logging.info(f"Failed to parse some children of operation {method} {path_name}. Got exception: {e}. "
                         f"Not an error for {self.__class__.__name__}, was passed.")

        self.operations.append(OperationMeta(op=operation, path=path_name, method=method))

    def add_k8s_path(self):
        patch_strategies_f_name = "patch_strategies_"
        for model in self.results:
            version, kind = None, None
            for f in model.fields:
                if f.name == "apiVersion":
                    version = f.default
                if f.name == "kind":
                    kind = f.default

            maybe_k8s_resource = version and kind
            if not maybe_k8s_resource:
                continue


            is_k8s_resource, patch_collected = False, False
            fields_to_add, field_types = {}, {}
            for meta in self.operations:

                # Skip endpoints with non-200 responses
                resp = meta.op.responses.get("200") or meta.op.responses.get("202")
                if not resp:
                    continue

                # Skip unknown response content
                content = resp.content.get("application/json")
                if not content:
                    continue

                # Skip references to other model (not our operation)
                ref = content.schema_.ref
                if not ref or not model.reference or not model.reference.path or not model.reference.path.endswith(ref):
                    continue

                # Build basic K8sResource fields from POST (create) queries for this model
                if meta.method == "post":
                    is_k8s_resource = True

                    # Add a few useful ClassVars
                    fields_to_add |= {
                        "plural_": meta.path.split('/')[-1],
                        "is_namespaced_": "namespaces/{namespace}/" in meta.path
                    }
                    for f in model.fields:
                        if f.name == "apiVersion":
                            fields_to_add["group_"] = f.default.split('/')[0] if "/" in f.default else None

                    for f in fields_to_add.keys():
                        if f == "group_":
                            field_types[f] = "ClassVar[Optional[str]]"
                        elif f == "is_namespaced_":
                            field_types[f] = "ClassVar[bool]"
                        else:
                            field_types[f] = "ClassVar[str]"

                # Build supported K8sResource update strategies from PATCH queries
                elif meta.method == "patch":
                    media_types = meta.op.requestBody.content.keys()
                    for m in media_types:
                        try:
                            PatchRequestType(m)
                        except ValueError:
                            logging.warning(f"Got unknown media type for PATCH in model {model.class_name}: {m}. "
                                            f"You must update your PatchRequestType enum spec!")

                    patch_strategies = {media for media in media_types}
                    fields_to_add |= {patch_strategies_f_name: _SortedSet(patch_strategies)}

                    # FixMe: Generator can't render enum values properly, so we render them as strings,
                    #  which makes IDE's typechecker puke
                    field_types |= {patch_strategies_f_name: "ClassVar[set[PatchRequestType]]"}
                    patch_collected = True

                # Skip everything else
                else:
                    continue

                if patch_collected:
                    break

            # K8sResource is the resource which can be created via POST request
            if not is_k8s_resource:
                # Also, drop all ClassVars in all sub-resources to avoid issue with from_dict() dynamic loading
                for f in model.fields:
                    if f.name in ["kind", "apiVersion"] and f.data_type == DataType(type="ClassVar[str]"):
                        f.data_type = DataType(type="str")
                continue

            for f_name, default in fields_to_add.items():
                model.fields.append(
                    self.data_model_field_type(
                        name=f_name,
                        default=default,
                        data_type=DataType(type=field_types[f_name]),
                        required=False,
                        constraints=None,
                        nullable=False,
                        strip_default_none=self.strip_default_none,
                        # extras={"metadata": {EXCLUDE_FIELD_META_KEY: True}},
                        use_annotated=self.use_annotated,
                        use_field_description=self.use_field_description,
                        use_default_kwarg=self.use_default_kwarg,
                        original_name=None,
                        has_default=True,
                        type_has_null=None
                    )
                )
            model.base_classes = [BaseClassDataType(type="K8sResource")]

        self.imports.append(IMPORT_CLASSVAR)

    def parse_raw(self) -> None:
        super().parse_raw()
        if not self.raw_obj.get("components") or "schemas" not in self.raw_obj.get("components") or {}:
            raise EmptyComponents()
        if OpenAPIScope.Paths in self.open_api_scopes:
            self.add_k8s_path()

    def parse_object_fields(self, obj: JsonSchemaObject, path: list[str], module_name: Optional[str] = None) \
            -> list[DataModelFieldBase]:
        fields = super().parse_object_fields(obj, path, module_name)
        is_k8s_resource = "x-kubernetes-group-version-kind" in obj.extras
        if is_k8s_resource:
            extras = obj.extras["x-kubernetes-group-version-kind"][0]
            group, version, kind = extras.get('group'), extras.get('version'), extras.get("kind")

        is_object_meta = path[-1].endswith(".meta.v1.ObjectMeta")

        for field in fields:
            # Do not allow mutable defaults in any case
            field.default = None if not field.default else field.default

            field.extras, dataclass_meta = field.extras or {}, {}
            if field.name != field.original_name:
                dataclass_meta["original_name"] = field.original_name
                field.extras |= {"metadata": dataclass_meta}

            if is_k8s_resource:
                if field.name == "kind":
                    field.data_type = DataType(type="ClassVar[str]")
                    field.default = kind
                    field.nullable = False
                    continue
                if field.name == "apiVersion":
                    field.data_type = DataType(type="ClassVar[str]")
                    field.default = f"{group}/{version}" if group else version
                    field.nullable = False
                    continue
                if field.name == "metadata":
                    field.nullable = False
                    field.extras |= {"default_factory": "ObjectMeta"}
                    continue

            # Find original property to get its merge key if any
            for name, prop in obj.properties.items():
                if name != field.original_name:
                    continue

                if is_object_meta:
                    if field.name == "labels":
                        field.extras |= {"default_factory": "dict"}
                        continue

                extras = prop.extras or {}
                if PATCH_STRATEGY in extras:
                    dataclass_meta[PATCH_STRATEGY] = extras[PATCH_STRATEGY]
                if PATCH_MERGE_KEY in extras:
                    dataclass_meta[PATCH_MERGE_KEY] = extras[PATCH_MERGE_KEY]

            if dataclass_meta:
                # Set defaults for optional fields forcibly. Original generator has a logical bug here
                # https://github.com/koxudaxi/datamodel-code-generator/blob/d2b89bb5fe8bbe27116db15c4b7c2b4735da4f85/src/datamodel_code_generator/model/dataclass.py#L123
                if not field.required:
                    if field.default is not None:
                        if type(field.default) in SCALAR_TYPES:
                            field.extras |= {"default": field.default}
                        else:
                            field.extras |= {"default_factory": str(field.default)}
                    else:
                        for non_scalar in NON_SCALAR_TYPES:
                            if field.type_hint.lower().startswith(non_scalar.__name__):
                                field.extras |= {"default_factory": non_scalar.__name__}
                                break
                        if "default_factory" not in field.extras:
                            field.extras |= {"default_factory": "lambda: None"}

                field.extras |= {"metadata": dataclass_meta}

        return fields

    # The function is bugged, so we drop _aliased types, and we support Python 3.10+ only anyway
    def _Parser__alias_shadowed_imports(self, *_, **__): pass
