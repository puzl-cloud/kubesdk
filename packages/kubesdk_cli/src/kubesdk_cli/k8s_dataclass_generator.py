from __future__ import annotations
import functools
import sys
import logging
import asyncio
import ast
import json
from pathlib import Path
import shutil
from typing import Dict, List, Tuple, Set
from urllib.parse import urlparse

from datamodel_code_generator import DataModelType, PythonVersion, LiteralType, OpenAPIScope

# Our own extended parser
from kubesdk_cli.k8s_schema_parser import generate, InputFileType, EmptyComponents

from kubesdk_cli.open_api_schema import safe_module_name, fetch_open_api_manifest, fetch_k8s_version


logging.basicConfig(level=logging.DEBUG, force=True, handlers=[logging.StreamHandler(sys.stdout)])


def _parse_exports_and_dataclasses(py_path: Path) -> Tuple[Set[str], Set[str]]:
    """
    Returns (exports, dataclasses) for a module file:
      - exports: __all__ if present (literal list/tuple of strings), else public top-level names
      - dataclasses: class names decorated with @dataclass / @dataclass(...)
    """
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))

    explicit_all: Set[str] | None = None
    public: Set[str] = set()
    dataclasses: Set[str] = set()

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


def write_inits_with_type_loader(base_dir: str | Path, extra_globals: List[str] = None) -> None:
    """
    Generate __init__.py files that:
      • do explicit imports `from .mod import A, B, ...`
      • wrap EXPORTED dataclasses: `A = __loader(A)` at package scope
      • wrap NON-EXPORTED dataclasses inside their own module (no re-export)
      • star-import subpackages (they do the same recursively)
      • emit __all__ with only exported names
    """
    logging.info(f"Writing __init__ for each submodule...")

    import os  # local import to keep function self-contained
    base = Path(base_dir).resolve()

    extra_globals = extra_globals or []
    loader_import: str = f"from {base.name}.loader import loader as __loader"

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

            # wrap exported dataclasses at package scope
            for cls in sorted(dcs & exports):
                wrap_exported_lines.append(f"{cls} = __loader({cls})")

            # wrap non-exported dataclasses inside their own module (no re-export)
            hidden = sorted(dcs - exports)
            if hidden:
                wrap_internal_lines.append(f"from . import {mod} as __mod_{mod}")
                for cls in hidden:
                    wrap_internal_lines.append(f"from .{mod} import {cls} as __{mod}_{cls}")
                    wrap_internal_lines.append(f"__mod_{mod}.{cls} = __loader(__{mod}_{cls})")
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
        content = (
            "# auto-generated: explicit re-exports; wrap dataclasses via loader()\n"
            "# flake8: noqa\n"
            f"{loader_import}\n\n"
            f"{imports_block}\n\n"
            f"{wrap_exported_block}\n\n"
            f"{all_line}\n\n" if all_line else ""
            f"{wrap_internal_block}\n\n"
            f"{subpkg_block}\n"
        )
        (pkg_dir / "__init__.py").write_text(content, encoding="utf-8")


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
        output: Path, python_version: PythonVersion, module_name: str, templates: Path, module_root: str,
        from_file: Path = None, url: str = None, http_headers: Dict[str, str] = None):
    try:
        assert from_file or url, "You must pass from_file path or OpenAPI schema url"
        await asyncio.to_thread(functools.partial(
            generate,
            input_=urlparse(url) if url else from_file,
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
                f"{module_root}.const.*",
                f"{module_root}.resource.*",
                f"{module_root}.loader.*"
            ],
            base_class=f"{module_root}.loader.LazyLoadModel",
            enum_field_as_literal=LiteralType.All,
            use_exact_imports=True,
            treat_dot_as_module=True,

            keyword_only=True,
            frozen_dataclasses=True,

            # FixMe: We should use reuse_model, but it's bugged for now:
            #  apis/controlplane.cluster.x-k8s.io/v1beta1: list object has no element 0
            reuse_model=False,
            use_union_operator=True
        ))
        logging.info(f"[ok]   {module_name} -> {output}")

    except EmptyComponents:
        logging.info(f"[skip] {module_name}: OpenAPI schema does not contain any components")
    except Exception as e:
        logging.warning(f"[skip] {module_name}: {e}")
        raise


async def generate_dataclasses_from_url(
        cluster_url: str, output: Path, templates: Path, python_version: PythonVersion = PythonVersion.PY_310,
        http_headers: Dict[str, str] = None) -> None:
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
            generate_for_schema(subdir.resolve(), python_version, label, templates, module_root=output.name,
                                url=url, http_headers=http_headers))

    await asyncio.gather(*tasks, return_exceptions=True)

    # Write Kubernetes API version finally
    version_file = "version.txt"
    logging.info(f"Writing Kubernetes version into {version_file} ...")
    k8s_version = fetch_k8s_version(cluster_url, http_headers)
    (output / version_file).write_text(f"{k8s_version}\n", encoding="utf-8")


def read_all_json_files(from_dir: Path | str, recursive: bool = True) -> Dict[str, Dict]:
    pattern = "**/*.json" if recursive else "*.json"
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in Path(from_dir).glob(pattern) if f.is_file()}

    
async def generate_dataclasses_from_dir(
        from_dir: Path, output: Path, templates: Path, python_version: PythonVersion = PythonVersion.PY_310) -> None:
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
            generate_for_schema(subdir.resolve(), python_version, schema_root, templates, module_root=output.name,
                                from_file=from_dir / api_schema_file))

    await asyncio.gather(*tasks, return_exceptions=True)

    # ToDo: Finish loading k8s version!
    # Write Kubernetes API version finally
    # version_file = "version.txt"
    # logging.info(f"Writing Kubernetes version into {version_file} ...")
    # k8s_version = fetch_k8s_version(cluster_url, http_headers)
    # (output / version_file).write_text(f"{k8s_version}\n", encoding="utf-8")


def prepare_module(module_path: Path, templates: Path, extra_globals: List[str] = None):
    extra_globals = extra_globals or []
    module_path.mkdir(parents=True, exist_ok=True)
    copy_file(templates / "init.py", module_path, "__init__.py")
    for file in extra_globals:
        copy_file(templates / file, module_path)
