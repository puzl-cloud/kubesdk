import os, builtins, ast, types, sys
from pathlib import Path
from typing import Dict, Tuple, Set, List, Optional, Union


def _parse_exports_and_dataclasses(py_path: Path) -> Tuple[Set[str], Set[str]]:
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))

    explicit_all: Optional[Set[str]] = None
    public: Set[str] = set()
    dataclasses: Set[str] = set()

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

    def _is_dc(dec: ast.AST) -> bool:
        if isinstance(dec, ast.Name): return dec.id == "dataclass"
        if isinstance(dec, ast.Attribute): return dec.attr == "dataclass"
        if isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Name): return f.id == "dataclass"
            if isinstance(f, ast.Attribute): return f.attr == "dataclass"
        return False

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(_is_dc(d) for d in node.decorator_list):
            dataclasses.add(node.name)

    exports = explicit_all if explicit_all is not None else public
    return set(exports), dataclasses


class NoImportLoader:
    """
    Executes .py files directly with a custom __import__ that resolves only sibling .py files
        - Registers a synthetic ModuleType in sys.modules before exec (critical for dataclasses)
        - Never executes any __init__.py
        - Supports relative imports
        - Handles circular imports via cache
    """
    def __init__(self, base_dir: Path):
        self.base = Path(base_dir).resolve()
        self.cache: Dict[Path, types.ModuleType] = {}  # path -> module

    def _resolve_relative(self, caller_file: Path, level: int, name: str) -> Path:
        parts = name.split(".") if name else []
        d = caller_file.parent
        for _ in range(max(0, level - 1)):  # level=1 same dir, 2 parent, etc.
            d = d.parent
        p = d.joinpath(*parts) if parts else d
        mod_path = p.with_suffix(".py")
        if mod_path.exists():
            return mod_path
        # we do NOT load packages / __init__.py — only plain modules
        raise ImportError(f"Relative import target not found: level={level}, name={name} from {caller_file}")

    def _get_or_exec(self, path: Path) -> types.ModuleType:
        path = path.resolve()
        if path in self.cache:
            return self.cache[path]

        # Create a unique synthetic module name under a private prefix
        mod_name = "__fileexec__." + ".".join(path.relative_to(self.base).with_suffix("").parts)
        module = types.ModuleType(mod_name)
        module.__file__ = str(path)
        module.__package__ = ""  # we don't rely on package semantics for our custom importer

        # Register BEFORE exec so dataclasses sees sys.modules[cls.__module__]
        sys.modules[mod_name] = module
        self.cache[path] = module

        real_import = builtins.__import__

        def custom_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Intercept only relative imports coming from our executed files
            if level and globals is not None and "__file__" in globals:
                caller_file = Path(globals["__file__"]).resolve()
                target_path = self._resolve_relative(caller_file, level, name)
                return self._get_or_exec(target_path)
            # Delegate absolute imports (stdlib/third-party)
            return real_import(name, globals, locals, fromlist, level)

        g = module.__dict__
        g["__builtins__"] = dict(builtins.__dict__)
        g["__builtins__"]["__import__"] = custom_import
        g["__name__"] = module.__name__ = mod_name
        g["__file__"] = module.__file__

        code = compile(path.read_text(encoding="utf-8"), filename=str(path), mode="exec")
        exec(code, g, g)
        return module

    def load_dataclasses(self) -> Dict[str, List[Tuple[Path, object]]]:
        """Return mapping: class name -> list of (file_path, cls_obj). Keeps duplicates."""
        results: Dict[str, List[Tuple[Path, object]]] = {}
        files: List[Path] = []
        for dirpath, _dirs, filenames in os.walk(self.base):
            for f in filenames:
                if f.endswith(".py") and f != "__init__.py":
                    files.append(Path(dirpath) / f)
        files.sort(key=lambda p: str(p.relative_to(self.base)))
        for f in files:
            _exports, dcs = _parse_exports_and_dataclasses(f)
            if not dcs:
                continue
            mod = self._get_or_exec(f)
            for cls_name in sorted(dcs):
                obj = getattr(mod, cls_name, None)
                if isinstance(obj, type):
                    results.setdefault(cls_name, []).append((f, obj))
        return results


def runtime_import_all_dataclasses_from_files(
        pkg_dir: Union[str, Path], target_globals: dict, set_cls_module_to_root: str,
        # "alias" | "first_wins" | "error"
        on_conflict: str = "alias") -> None:
    """
    Executes every .py under pkg_dir with a local importer (no __init__),
    finds @dataclass classes, and injects them into target_globals.
    """
    base = Path(pkg_dir).resolve()
    loader = NoImportLoader(base)
    all_dcs = loader.load_dataclasses()

    existing_all = list(target_globals.get("__all__", []))
    seen_all = set(existing_all)
    injected: List[str] = []

    # Keep first class name as-is; alias additional duplicates
    for cls_name in sorted(all_dcs.keys()):
        occurrences = all_dcs[cls_name]
        for idx, (path, cls) in enumerate(occurrences):
            if idx == 0 and cls_name not in target_globals:
                name = cls_name
            else:
                if on_conflict == "alias":
                    rel = path.with_suffix("").relative_to(base)
                    base_alias = f"{'_'.join(rel.parts)}__{cls_name}"
                    alias = base_alias; i = 2
                    while alias in target_globals:
                        alias = f"{base_alias}_{i}"; i += 1
                    name = alias
                elif on_conflict == "first_wins":
                    continue
                else:
                    raise ValueError(f"Duplicate dataclass name: {cls_name} from {path}")

            cls.__module__ = set_cls_module_to_root
            target_globals[name] = cls
            if name not in seen_all:
                injected.append(name); seen_all.add(name)

    if injected:
        target_globals["__all__"] = existing_all + injected
