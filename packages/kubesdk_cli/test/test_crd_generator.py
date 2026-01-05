import importlib
import sys
import unittest
import tempfile
from pathlib import Path

import yaml

from kubesdk.crd import CustomK8sResourceDefinition
from kubesdk_cli.crd_generator import discover_crd_definitions, generate_crd, _is_valid_ident, _iter_module_targets


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestCrdGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        self._sys_path_before = list(sys.path)
        self._modules_before = set(sys.modules.keys())

    def tearDown(self) -> None:
        # Drop modules imported from the temp scan root + fallback scan modules.
        for name in list(sys.modules.keys()):
            if name in self._modules_before:
                continue
            if name.startswith("_kubesdk_scan_"):
                sys.modules.pop(name, None)
                continue
            mod = sys.modules.get(name)
            mod_file = getattr(mod, "__file__", None)
            if mod_file is None:
                continue
            try:
                if Path(mod_file).resolve().is_relative_to(self.root):
                    sys.modules.pop(name, None)
            except Exception:
                # Conservative cleanup on older Python / weird __file__
                if str(mod_file).startswith(str(self.root)):
                    sys.modules.pop(name, None)

        sys.path[:] = self._sys_path_before
        importlib.invalidate_caches()
        self._td.cleanup()

    def _names(self, classes) -> set[str]:
        return {c.__name__ for c in classes}

    def test_is_valid_ident(self) -> None:
        self.assertTrue(_is_valid_ident("abc"))
        self.assertTrue(_is_valid_ident("_a1"))
        self.assertFalse(_is_valid_ident("1abc"))
        self.assertFalse(_is_valid_ident("a-b"))
        self.assertFalse(_is_valid_ident("class"))

    def test_iter_module_targets(self) -> None:
        _write(self.root, "scan_one.py", "x=1\n")
        _write(self.root, "bad-name.py", "x=2\n")
        _write(self.root, "pkg/__init__.py", "x=3\n")
        _write(self.root, "__init__.py", "x=4\n")  # ignored by your logic
        _write(self.root, "ns/inner.py", "x=5\n")

        targets = _iter_module_targets(self.root)
        mapping = {p.relative_to(self.root).as_posix(): name for name, p in targets}

        self.assertEqual(mapping["scan_one.py"], "scan_one")
        self.assertIsNone(mapping["bad-name.py"])
        self.assertEqual(mapping["pkg/__init__.py"], "pkg")
        self.assertEqual(mapping["ns/inner.py"], "ns.inner")
        self.assertNotIn("__init__.py", mapping)

    def test_discover_single_file(self) -> None:
        _write(
            self.root,
            "scan_one.py",
            """
from kubesdk.crd import CustomK8sResourceDefinition
class MyCRD(CustomK8sResourceDefinition):
    pass
""".lstrip(),
        )

        got = discover_crd_definitions(self.root)
        self.assertIn("MyCRD", self._names(got))
        cls = next(c for c in got if c.__name__ == "MyCRD")
        self.assertEqual(cls.__module__, "scan_one")
        self.assertTrue(issubclass(cls, CustomK8sResourceDefinition))

    def test_discover_namespace_nested_without_init(self) -> None:
        _write(
            self.root,
            "ns/inner.py",
            """
from kubesdk.crd import CustomK8sResourceDefinition
class InnerCRD(CustomK8sResourceDefinition):
    pass
""".lstrip(),
        )

        got = discover_crd_definitions(self.root)
        self.assertIn("InnerCRD", self._names(got))
        cls = next(c for c in got if c.__name__ == "InnerCRD")
        self.assertEqual(cls.__module__, "ns.inner")

    def test_discover_pkg_init_as_pkg(self) -> None:
        _write(
            self.root,
            "pkg/__init__.py",
            """
from kubesdk.crd import CustomK8sResourceDefinition
class PkgCRD(CustomK8sResourceDefinition):
    pass
""".lstrip(),
        )

        got = discover_crd_definitions(self.root)
        self.assertIn("PkgCRD", self._names(got))
        cls = next(c for c in got if c.__name__ == "PkgCRD")
        self.assertEqual(cls.__module__, "pkg")

    def test_invalid_filename_imports_via_fallback(self) -> None:
        _write(
            self.root,
            "bad-name.py",
            """
from kubesdk.crd import CustomK8sResourceDefinition
class BadNameCRD(CustomK8sResourceDefinition):
    pass
""".lstrip(),
        )

        got = discover_crd_definitions(self.root)
        self.assertIn("BadNameCRD", self._names(got))
        cls = next(c for c in got if c.__name__ == "BadNameCRD")
        self.assertTrue(cls.__module__.startswith("_kubesdk_scan_"))

    def test_reexports_ignored(self) -> None:
        _write(
            self.root,
            "scan_b.py",
            """
from kubesdk.crd import CustomK8sResourceDefinition
class RealCRD(CustomK8sResourceDefinition):
    pass
""".lstrip(),
        )
        _write(self.root, "scan_a.py", "from scan_b import RealCRD\n")

        got = discover_crd_definitions(self.root)
        self.assertIn("RealCRD", self._names(got))
        cls = next(c for c in got if c.__name__ == "RealCRD")
        self.assertEqual(cls.__module__, "scan_b")

    def test_bare_imports_resolve_from_root(self) -> None:
        _write(self.root, "scan_dep.py", "VALUE = 123\n")
        _write(
            self.root,
            "scan_user.py",
            """
import scan_dep
from kubesdk.crd import CustomK8sResourceDefinition
class UsesDep(CustomK8sResourceDefinition):
    v = scan_dep.VALUE
""".lstrip(),
        )

        got = discover_crd_definitions(self.root)
        self.assertIn("UsesDep", self._names(got))
        cls = next(c for c in got if c.__name__ == "UsesDep")
        self.assertEqual(cls.v, 123)

    def test_generate_crd_uses_metadata_name(self) -> None:
        # Fake "CRD object" with metadata.name and to_dict(drop_nones=True)
        class _Meta:
            def __init__(self, name: str):
                self.name = name

        class _CrdObj:
            def __init__(self, name: str):
                self.metadata = _Meta(name)

            def to_dict(self, drop_nones: bool = False):
                return {"apiVersion": "v1", "kind": "CustomResourceDefinition", "metadata": {"name": self.metadata.name}}

        class Def(CustomK8sResourceDefinition):
            def build(self):
                return _CrdObj("example.my.domain")

        out = self.root / "out"
        path = generate_crd(Def, out_dir=out)
        self.assertTrue(path.exists())
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(data["metadata"]["name"], "example.my.domain")

    def test_generate_crd_defaults_to_class_name_if_no_metadata_name(self) -> None:
        class _CrdObj:
            metadata = None

            def to_dict(self, drop_nones: bool = False):
                return {"x": 1}

        class DefNoName(CustomK8sResourceDefinition):
            def build(self):
                return _CrdObj()

        out = self.root / "out2"
        path = generate_crd(DefNoName, out_dir=out)
        self.assertEqual(path.name, "DefNoName.yaml")
