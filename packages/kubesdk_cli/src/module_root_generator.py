from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Union, Set
import importlib.util
import os
import sys
import textwrap
from string import Template


def _load_parser_module() -> object:
    """
    :returns: The loaded `import_all_dataclasses` module located next to this file.
    :raises FileNotFoundError: If `import_all_dataclasses.py` is not found.
    """
    here = Path(__file__).resolve().parent
    parser_path = here / "import_all_dataclasses.py"
    if not parser_path.exists():
        raise FileNotFoundError(f"import_all_dataclasses.py not found next to {__file__}")
    spec = importlib.util.spec_from_file_location("import_all_dataclasses", str(parser_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["import_all_dataclasses"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _discover_classes_by_parsing(models_dir: Path) -> Dict[str, List[Tuple[Path, str]]]:
    """
    :param models_dir: Path to the package's `./models` directory.
    :returns: Mapping `{ClassName: [(file_path, ClassName), ...]}` discovered by AST parsing.
    """
    mod = _load_parser_module()
    parse = getattr(mod, "_parse_exports_and_dataclasses")

    py_files: List[Path] = []
    for dirpath, _dirs, files in os.walk(models_dir):
        for f in files:
            if f.endswith(".py"):
                py_files.append(Path(dirpath) / f)
    py_files.sort(key=lambda p: str(p.relative_to(models_dir)))

    classes: Dict[str, List[Tuple[Path, str]]] = {}
    for f in py_files:
        _exports, dcs = parse(f)
        for name in sorted(dcs):
            classes.setdefault(name, []).append((f, name))
    return classes


def _alias_for(models_dir: Path, path: Path, class_name: str, existing: Set[str]) -> str:
    """
    :param models_dir: Base `./models` directory used for relative path computation.
    :param path: Source file path for the dataclass definition.
    :param class_name: Original dataclass name.
    :param existing: Set of already-chosen export names to avoid collisions.
    :returns: A unique alias of the form `<rel_path_joined>__<ClassName>[_N]`.
    """
    rel = path.with_suffix("").relative_to(models_dir)
    base_alias = f"{'_'.join(rel.parts)}__{class_name}"
    alias = base_alias
    i = 2
    while alias in existing:
        alias = f"{base_alias}_{i}"
        i += 1
    return alias


def _build_name_to_origin_literal(entries: List[Tuple[str, str, str]]) -> str:
    """
    :param entries: list of (export_name, module_dotted, class_name)
    :returns: Pretty-printed dict literal as a string.
    """
    if not entries:
        return "{}"
    items = []
    for export_name, mod, cls in entries:
        items.append(f"{export_name!r}: ({mod!r}, {cls!r})")
    return "{\n    " + ",\n    ".join(items) + "\n}"


_INIT_TEMPLATE = Template(textwrap.dedent("""
# auto-generated: avoiding circular imports
from __future__ import annotations
import importlib as __importlib

__all__ = $all_list

# Map exported name -> (module, class_name)
NAME_TO_ORIGIN = $name_to_origin

_CACHE: dict[str, object] = {}

def __getattr__(name: str):
    if name in _CACHE:
        return _CACHE[name]
    try:
        mod_name, cls_name = NAME_TO_ORIGIN[name]
    except KeyError as e:
        raise AttributeError(name) from e
    mod = __importlib.import_module(mod_name)
    obj = getattr(mod, cls_name)
    try:
        obj.__module__ = __name__
    except Exception:
        # Some objects may not allow reassignment; ignore.
        pass
    _CACHE[name] = obj
    return obj

def __dir__():
    return sorted(list(globals().keys()) + list(NAME_TO_ORIGIN.keys()))
"""))


def generate_module_root(models_dir: Union[str, Path], package_root_name: str, on_conflict: str = "alias") -> str:
    """
    :param models_dir: Path to the package's `./models` folder.
    :param package_root_name: Root package name, e.g. `"k8s_models"`.
    :param on_conflict:
        How to handle duplicate class names:
            - `"alias"` (default): generate unique aliases
            - `"first_wins"`: keep first occurrence only
            - `"error"`: raise on duplicates
    :returns: The rendered, lazy `__init__.py` content as a string.
    :raises ValueError: If `on_conflict` is invalid or when duplicates are found with `"error"`.
    """
    if on_conflict not in {"alias", "first_wins", "error"}:
        raise ValueError("on_conflict must be 'alias', 'first_wins', or 'error'")

    models_dir = Path(models_dir).resolve()
    classes = _discover_classes_by_parsing(models_dir)

    export_order: List[str] = []
    seen: Set[str] = set()
    entries: List[Tuple[str, str, str]] = []  # (export_name, module_path, class_name)

    # Deterministic: class names sorted; occurrences in each class sorted by path (already).
    for cls_name in sorted(classes.keys()):
        occurrences = classes[cls_name]
        for idx, (path, _cls) in enumerate(occurrences):
            rel_mod = path.with_suffix("").relative_to(models_dir)
            mod_dotted = f"{package_root_name}.models.{'.'.join(rel_mod.parts)}"
            if idx == 0 and cls_name not in seen:
                export_name = cls_name
            else:
                if on_conflict == "alias":
                    export_name = _alias_for(models_dir, path, cls_name, seen)
                elif on_conflict == "first_wins":
                    continue
                else:
                    # on_conflict == "error"
                    raise ValueError(f"Duplicate dataclass name: {cls_name} from {path}")
            entries.append((export_name, mod_dotted, cls_name))
            export_order.append(export_name)
            seen.add(export_name)

    name_to_origin_literal = _build_name_to_origin_literal(entries)

    rendered = _INIT_TEMPLATE.substitute(
        all_list=repr(export_order),
        name_to_origin=name_to_origin_literal,
    )
    return rendered
