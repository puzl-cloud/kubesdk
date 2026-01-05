from __future__ import annotations

import importlib
import importlib.util
import keyword
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import yaml

from kubesdk.crd import CustomK8sResourceDefinition


_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def _is_valid_ident(part: str) -> bool:
    return bool(_IDENT_RE.match(part)) and not keyword.iskeyword(part)


@contextmanager
def _sys_path_import_root(root: Path):
    root = root.resolve()
    old = list(sys.path)
    try:
        # Put root at the very front so "import foo" prefers root/foo.py.
        sys.path[:] = [str(root)] + [p for p in sys.path if p != str(root)]
        importlib.invalidate_caches()
        yield
    finally:
        sys.path[:] = old
        importlib.invalidate_caches()


def _iter_module_targets(root: Path) -> list[tuple[str | None, Path]]:
    """
    Returns (module_name, file_path).
    module_name is None when the path can't be represented as a valid import name.
    """
    targets: list[tuple[str | None, Path]] = []
    for py in root.rglob("*.py"):
        if not py.is_file():
            continue

        rel = py.relative_to(root)
        parts = list(rel.with_suffix("").parts)

        # pkg/__init__.py -> module "pkg"
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                # root/__init__.py -> no meaningful module name
                continue

        if parts and all(_is_valid_ident(p) for p in parts):
            targets.append((".".join(parts), py))
        else:
            targets.append((None, py))

    # Import shallow modules first (helps when modules expect parents to exist)
    targets.sort(key=lambda t: ((t[0] or "").count("."), t[0] or t[1].as_posix()))
    return targets


def _import_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _force_import(modname: str, expected_file: Path):
    """
    Import modname, but if a module with that name is already loaded from a different file,
    drop it and import ours.
    """
    existing = sys.modules.get(modname)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file is None or Path(existing_file).resolve() != expected_file.resolve():
            sys.modules.pop(modname, None)

    try:
        return importlib.import_module(modname)
    except Exception as e:
        raise ImportError(f"Failed importing {modname} from {expected_file}: {e}") from e


def discover_crd_definitions(search_dir: Path | str) -> list[type[CustomK8sResourceDefinition]]:
    """
    Import every python file under search_dir (recursively), with imports resolved
    as if search_dir is import-root. Returns local subclasses of CustomK8sResourceDefinition.
    """
    root = Path(search_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    found: dict[tuple[str, str], type[CustomK8sResourceDefinition]] = {}
    targets = _iter_module_targets(root)

    with _sys_path_import_root(root):
        imported = []
        for modname, path in targets:
            if modname is not None:
                imported.append(_force_import(modname, path))
            else:
                # fallback: still import the file so we can discover classes inside it
                unique = f"_kubesdk_scan_{abs(hash(path.as_posix()))}"
                imported.append(_import_module_from_path(unique, path))

        for mod in imported:
            for obj in vars(mod).values():
                if not isinstance(obj, type):
                    continue
                if obj is CustomK8sResourceDefinition:
                    continue
                if not issubclass(obj, CustomK8sResourceDefinition):
                    continue
                # Keep only classes defined in that module (not re-exports)
                if getattr(obj, "__module__", None) != mod.__name__:
                    continue
                found[(obj.__module__, obj.__qualname__)] = obj

    return list(found.values())


def generate_crd(resource_definition: type[CustomK8sResourceDefinition], out_dir: Path) -> Path:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    crd = resource_definition().build()
    data = crd.to_dict(drop_nones=True)
    name = getattr(getattr(crd, "metadata", None), "name", None) or resource_definition.__name__
    base = out_dir / name
    path = base.with_suffix(".yaml")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
