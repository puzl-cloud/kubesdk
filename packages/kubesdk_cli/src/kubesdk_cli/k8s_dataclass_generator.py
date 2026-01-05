from __future__ import annotations
import functools
import sys
import logging
import asyncio
import ast
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone
from urllib.parse import urlparse

from datamodel_code_generator import DataModelType, PythonVersion, LiteralType, OpenAPIScope

# Our own extended parser
from kubesdk_cli.k8s_schema_parser import generate, InputFileType, EmptyComponents
from kubesdk_cli.open_api_schema import safe_module_name, fetch_open_api_manifest, fetch_k8s_version
from kubesdk_cli.const import *


logging.basicConfig(level=logging.DEBUG, force=True, handlers=[logging.StreamHandler(sys.stdout)])


def _parse_exports_and_dataclasses(py_path: Path) -> tuple[set[str], set[str]]:
    """
    Returns (exports, dataclasses) for a module file:
      - exports: __all__ if present (literal list/tuple of strings), else public top-level names
      - dataclasses: class names decorated with @dataclass / @dataclass(...)
    """
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))

    explicit_all: set[str] | None = None
    public: set[str] = set()
    dataclasses: set[str] = set()

    # Fallback public names (no __all__): classes, funcs, assignments not starting with "_"
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    public.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                public.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                public.add(node.name)

    # __all__ if literal list/tuple of strings
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        names: list[str] = []
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.append(elt.value)
                        explicit_all = set(names)

    def _is_dataclass_dec(dec: ast.AST) -> bool:
        # @dataclass or @dataclass(...)
        if isinstance(dec, ast.Name):
            return dec.id == "dataclass"
        if isinstance(dec, ast.Attribute):
            return dec.attr == "dataclass"
        if isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Name):
                return f.id == "dataclass"
            if isinstance(f, ast.Attribute):
                return f.attr == "dataclass"
        return False

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if any(_is_dataclass_dec(d) for d in node.decorator_list):
                dataclasses.add(node.name)

    exports = explicit_all if explicit_all is not None else public
    return set(exports), dataclasses


def write_inits(base_dir: str | Path, extra_globals: list[str] = None) -> None:
    """
    Generate __init__.py files that:
      • do explicit imports `from .mod import A, B, ...`
      • wrap NON-EXPORTED dataclasses inside their own module (no re-export)
      • star-import subpackages (they do the same recursively)
      • emit __all__ with only exported names
    """
    logging.info(f"Writing __init__ for each submodule...")

    import os  # local import to keep function self-contained
    base = Path(base_dir).expanduser().resolve()

    extra_globals = extra_globals or []

    for root, dirs, files in os.walk(base):
        pkg_dir = Path(root)
        init_path = pkg_dir / "__init__.py"
        if not init_path.exists():
            continue

        logging.info(f"Writing {init_path}")

        # Child modules and subpackages
        module_paths = sorted(
            (pkg_dir / f) for f in files
            if f.endswith(".py") and f not in ["__init__.py"] + [extra_file for extra_file in extra_globals])
        subpkg_names = sorted(d for d in dirs if (pkg_dir / d / "__init__.py").exists())

        # Build explicit imports + wrap directives
        all_exports: list[str] = []
        import_lines: list[str] = []
        wrap_exported_lines: list[str] = []
        wrap_internal_lines: list[str] = []

        for mp in module_paths:
            mod = mp.stem
            exports, dcs = _parse_exports_and_dataclasses(mp)

            # explicit import line for exports (keeps IDEs happy)
            if exports:
                names = ", ".join(sorted(exports))
                import_lines.append(f"from .{mod} import {names}")
                all_exports.extend(sorted(exports))

            # wrap non-exported dataclasses inside their own module (no re-export)
            hidden = sorted(dcs - exports)
            if hidden:
                wrap_internal_lines.append(f"from . import {mod} as __mod_{mod}")
                for cls in hidden:
                    wrap_internal_lines.append(f"from .{mod} import {cls} as __{mod}_{cls}")
                    wrap_internal_lines.append(f"del __{mod}_{cls}")
                wrap_internal_lines.append(f"del __mod_{mod}")

        # Package __all__
        if all_exports:
            seen = set()
            unique = [n for n in all_exports if not (n in seen or seen.add(n))]
            all_line = "__all__ = [" + ", ".join(f"'{n}'" for n in unique) + "]"
        else:
            # Skip this __init__ if there is nothing to export anyway
            continue

        # subpackage star imports
        subpkg_lines = [f"from .{sp} import *" for sp in subpkg_names]

        # Precompute blocks to avoid backslashes in f-string expressions
        imports_block = "\n".join(import_lines).rstrip()
        wrap_exported_block = "\n".join(wrap_exported_lines).rstrip()
        wrap_internal_block = "\n".join(wrap_internal_lines).rstrip()
        subpkg_block = "\n".join(subpkg_lines).rstrip()

        # Build final content
        double_line = "\n\n"
        content = f"""{GENERATED_HEADER}\
{imports_block}\n\n
{wrap_exported_block}\n
{all_line + double_line if all_line else ""}\
{wrap_internal_block + double_line if wrap_exported_block else ""}\
{subpkg_block}\
"""
        init_path.write_text(content, encoding="utf-8")


# ToDo: Move it into separate python file, need to solve dynamic meta import problem somehow
def write_base_resource_py(base_dir: str | Path, module_name: str, meta_version: str):
    base = Path(base_dir).expanduser().resolve()
    resource_py_path = base / "_k8s_resource_base.py"
    logging.info(f"Writing base resource models at {resource_py_path}")

    content = f"""{GENERATED_HEADER}\
import sys
from typing import ClassVar, Optional, Set, List, Dict, Any, Type
from dataclasses import dataclass, field
from typing import TypeVar, Generic

if sys.version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self

from .loader import Loadable, _LOAD_LAZY_FIELD, _LOAD_TYPES_ON_INIT
from .const import *

from {module_name}.api_{meta_version}.io.k8s.apimachinery.pkg.apis.meta import ObjectMeta, ListMeta
""" + """

_DYNAMIC_CLASS_VARS = ["apiVersion", "kind"]


@dataclass(slots=True, kw_only=True, frozen=True)
class K8sResource(Loadable):
    apiVersion: ClassVar[str]
    kind: ClassVar[str]
    metadata: ObjectMeta

    # OpenAPI fields which are not a part of the resource model
    plural_: ClassVar[str]
    group_: ClassVar[str]
    
    # Defaults for any resource including CRDs
    patch_strategies_: ClassVar[Set[PatchRequestType]] = {
        PatchRequestType.json,
        PatchRequestType.server_side_cbor,
        PatchRequestType.server_side,
        PatchRequestType.merge
    }
    is_namespaced_: ClassVar[bool]
    
    @classmethod
    def from_dict(cls, src: Dict[str, Any], lazy: bool = True) -> Self:
        for var in _DYNAMIC_CLASS_VARS:
            if var in src:
                del src[var]
        return cls(**src | {_LOAD_LAZY_FIELD: lazy, _LOAD_TYPES_ON_INIT: True})
        
    @classmethod
    def api_path(cls) -> str: 
        try:
            return cls.__api_path
        except AttributeError:
            version = cls.apiVersion.split("/", 1)[-1]
            base = f"apis/{cls.group_}/{version}" if cls.group_ else f"api/{version}"
            namespaced_path = "/namespaces/{namespace}" if cls.is_namespaced_ else ""
            cls.__api_path = f"{base}{namespaced_path}/{cls.plural_}"
            return cls.__api_path

    def to_dict(self, drop_nones: bool = False) -> Dict[str, Any]:
        res = super(K8sResource, self).to_dict(drop_nones)
        for var in _DYNAMIC_CLASS_VARS:
            res[var] = getattr(self, var)
        return res
        

def _bind_class_vars_from_original_kind(cls, params) -> Type:
    T = params[0] if isinstance(params, tuple) else params
    with cls._type_cache_lock:
        cached = cls._type_cache.get(T)
        if cached is not None:
            return cached

        # If vars are already concretely set on this class, don't override
        if all(cls.__dict__.get(var) is not None for var in _DYNAMIC_CLASS_VARS):
            cls._type_cache[T] = cls
            return cls

        # Check if something is unset in passed T class and do not inherit anything
        kw = {var: getattr(T, var, None) for var in _DYNAMIC_CLASS_VARS}
        for var, val in kw.items():
            if val is None:
                return cls

        # Modify kind to default, if it wasn't set on this class
        if not cls.__dict__.get("kind"):
            kw["kind"] = f"{kw['kind']}List"

        name = f"{cls.__name__}[{getattr(T, '__name__', repr(T))}]"
        specialized = type(name, (cls,), kw | {"__resource_type__": T})
        cls._type_cache[T] = specialized
        return specialized
"""
    resource_py_path.write_text(content, encoding="utf-8")


def is_openapi_v3_with_models(root: object) -> bool:
    """Accept only OpenAPI v3 docs that actually define component schemas."""
    if not isinstance(root, dict):
        return False
    v = root.get("openapi")
    if not (isinstance(v, str) and v.startswith("3.")):
        return False
    comps = root.get("components")
    if not isinstance(comps, dict):
        return False
    schemas = comps.get("schemas")
    if not isinstance(schemas, dict):
        return False
    return len(schemas) > 0


def copy_file(src: Path, dst_dir: Path, new_name: str = None) -> Path:
    """Copy file `src` into directory `dst_dir`, returning the destination path."""
    new_name = new_name or src.name
    if not src.is_file():
        raise FileNotFoundError(f"Not a file: {src}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / new_name
    shutil.copy2(src, dst)
    return dst


async def generate_for_schema(
        output: Path, python_version: PythonVersion, templates: Path, module_name: str,
        from_file: Path = None, url: str = None, http_headers: dict[str, str] = None):
    input_ = urlparse(url) if url else from_file
    try:
        assert input_, "You must pass from_file path or OpenAPI schema url"
        await asyncio.to_thread(functools.partial(
            generate,
            input_=input_,
            input_file_type=InputFileType.OpenAPIK8s,
            openapi_scopes=[OpenAPIScope.Paths, OpenAPIScope.Schemas],
            output=output,

            #
            # OpenAPI parsing settings
            use_annotated=False,
            field_constraints=False,
            http_headers=http_headers if url and http_headers else None,

            #
            # Python code settings
            custom_template_dir=templates,
            output_model_type=DataModelType.DataclassesDataclass,
            target_python_version=python_version,

            additional_imports=[
                "datetime.datetime",
                "datetime.timezone",
                "typing.Set",
                f"{module_name}.const.*",
                f"{module_name}.resource.*",
                f"{module_name}.loader.*"
            ],
            base_class=f"{module_name}.loader.Loadable",
            enum_field_as_literal=LiteralType.All,
            use_exact_imports=True,
            treat_dot_as_module=True,

            keyword_only=True,
            frozen_dataclasses=True,

            # We do this to generate sets from ordered lists for code consistency
            use_unique_items_as_set=True,

            # 3.10+ only
            use_union_operator=True,

            # FixMe: We should use reuse_model, but it's bugged for now:
            #  apis/controlplane.cluster.x-k8s.io/v1beta1: list object has no element 0
            reuse_model=False
        ))
        logging.info(f"[ok]   {input_} -> {output}")

    except EmptyComponents:
        logging.info(f"[skip] {input_}: OpenAPI schema does not contain any components")
    except Exception as e:
        logging.warning(f"[skip] {input_}: {e}")
        raise


async def generate_dataclasses_from_url(
        cluster_url: str, output: Path, templates: Path, module_name: str,
        python_version: PythonVersion = PythonVersion.PY_310, http_headers: dict[str, str] = None) -> None:
    """
    Iterate a downloader manifest (label -> {file, source_url}) and run codegen per URL.
    Each label gets its own subpackage under output dir.
    """

    # Get OpenAPI v3 manifest
    logging.info(f"Generating dataclasses from Kubernetes cluster {cluster_url}")

    cluster_url = cluster_url.strip("/")
    manifest = fetch_open_api_manifest(cluster_url, http_headers)

    tasks = []
    for label, meta in sorted(manifest["paths"].items()):
        url = f"{cluster_url}{meta.get('serverRelativeURL')}"
        subdir = output / safe_module_name(label)
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "__init__.py").touch(exist_ok=True)
        tasks.append(
            generate_for_schema(subdir.expanduser().resolve(), python_version, templates, module_name=module_name,
                                url=url, http_headers=http_headers))

    await asyncio.gather(*tasks, return_exceptions=True)

    # ToDo: Add k8s versioning to understand the range of compatible Kubernetes APIs for each model


def read_all_json_files(from_dir: Path | str, recursive: bool = True) -> dict[str, dict]:
    pattern = "**/*.json" if recursive else "*.json"
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in Path(from_dir).glob(pattern) if f.is_file()}


async def generate_dataclasses_from_dir(
        from_dir: Path, output: Path, templates: Path, module_name: str,
        python_version: PythonVersion = PythonVersion.PY_310) -> None:
    """
    Iterate a downloader manifest (label -> {file, source_url}) and run codegen per URL.
    Each label gets its own subpackage under output dir.
    """
    logging.info(f"Generating dataclasses from OpenAPI schema {from_dir}")
    all_schemas = read_all_json_files(from_dir)
    if not all_schemas:
        raise FileNotFoundError(f"No OpenAPI schemas found in {from_dir}")

    tasks = []
    for api_schema_file, meta in all_schemas.items():
        try:
            schema_root = min(meta.get("paths"))  # first path of the schema
        except Exception:
            logging.error(f"[skip] {from_dir / api_schema_file} is not a valid OpenAPI schema: unable to read paths")
            continue

        subdir = output / safe_module_name(schema_root)
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "__init__.py").touch(exist_ok=True)
        tasks.append(
            generate_for_schema(subdir.expanduser().resolve(), python_version, templates, module_name=module_name,
                                from_file=from_dir / api_schema_file))

    await asyncio.gather(*tasks, return_exceptions=True)

    # ToDo: Add k8s versioning to understand the range of compatible Kubernetes APIs for each model


def prepare_module(module_path: Path, templates: Path, extra_globals: list[str] = None):
    extra_globals = extra_globals or []
    module_path.mkdir(parents=True, exist_ok=True)
    for file in extra_globals:
        copy_file(templates / file, module_path)


def finalize_module_init(module_path: Path, templates: Path):
    copy_file(templates / "init.py", module_path, "__init__.py")
