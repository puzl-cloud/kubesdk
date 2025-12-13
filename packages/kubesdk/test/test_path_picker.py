import unittest

from kubesdk._path.picker import (
    PathRoot,
    PathPicker,
    PathResolutionError,
    _resolve_segment,
    from_root_,
    path_
)
from kubesdk._patch.json_patch import escape_json_path_pointer_token


class TestPathRoot(unittest.TestCase):
    def test_getattr_builds_segments(self):
        root = PathRoot()
        new_root = root.spec.template.metadata
        self.assertIsInstance(new_root, PathRoot)
        self.assertEqual(new_root._segments, ["spec", "template", "metadata"])

    def test_getattr_rejects_dunder_and_private(self):
        root = PathRoot()
        with self.assertRaises(AttributeError):
            _ = root.__dict__
        with self.assertRaises(AttributeError):
            _ = root._private

    def test_getitem_int_and_str_and_errors(self):
        root = PathRoot()
        # positive int index
        self.assertEqual(root[0]._segments, [0])
        # string index
        self.assertEqual(root["name"]._segments, ["name"])
        # negative index is forbidden
        with self.assertRaises(IndexError):
            _ = root[-1]
        # unsupported type
        with self.assertRaises(TypeError):
            _ = root[1.5]

    def test_call_not_allowed(self):
        root = PathRoot()
        with self.assertRaises(TypeError):
            root()

    def test_repr(self):
        root = PathRoot()
        self.assertIn("PathRoot([])", repr(root))


class TestResolveSegment(unittest.TestCase):
    def test_int_segment_list_and_mapping_and_errors(self):
        # list access success
        self.assertEqual(_resolve_segment([10, 20], 1, index=0, segments=[1]), 20)

        # non-list (mapping) via __getitem__ in the int branch
        self.assertEqual(_resolve_segment({0: "x"}, 0, index=0, segments=[0]), "x")

        # IndexError wrapped into PathResolutionError
        with self.assertRaises(PathResolutionError) as cm_idx:
            _resolve_segment([1, 2], 10, index=0, segments=[10])
        self.assertIsInstance(cm_idx.exception.cause, IndexError)

        # KeyError wrapped into PathResolutionError
        with self.assertRaises(PathResolutionError) as cm_key:
            _resolve_segment({}, 1, index=0, segments=[1])
        self.assertIsInstance(cm_key.exception.cause, KeyError)

        # TypeError wrapped into PathResolutionError (non-subscriptable)
        with self.assertRaises(PathResolutionError) as cm_type:
            _resolve_segment(123, 0, index=0, segments=[0])
        self.assertIsInstance(cm_type.exception.cause, TypeError)

    def test_str_segment_mapping_paths(self):
        # Mapping, key present
        mapping = {"foo": "bar"}
        self.assertEqual(
            _resolve_segment(mapping, "foo", index=0, segments=["foo"]),
            "bar",
        )

        # Mapping, attr present but key missing -> getattr branch
        class MappingWithAttr(dict):
            extra_attr = "value"

        m2 = MappingWithAttr()
        result = _resolve_segment(m2, "extra_attr", index=0, segments=["extra_attr"])
        self.assertEqual(result, "value")

        # Mapping, neither key nor attr -> inner PathResolutionError via e_item
        with self.assertRaises(PathResolutionError) as cm:
            _resolve_segment({}, "missing", index=0, segments=["missing"])
        self.assertIsInstance(cm.exception.cause, KeyError)

        # Ensure message has segment, pointer and type
        msg = str(cm.exception)
        self.assertIn("missing", msg)
        self.assertIn("after /", msg)
        self.assertIn("dict", msg)

    def test_str_segment_non_mapping_paths(self):
        # Non-mapping, attribute present
        class Obj:
            foo = "attr-value"

        o = Obj()
        self.assertEqual(
            _resolve_segment(o, "foo", index=0, segments=["foo"]),
            "attr-value",
        )

        # Non-mapping, attr missing but __getitem__ works
        class ItemOnly:
            def __getitem__(self, key):
                return f"item-{key}"

        io = ItemOnly()
        self.assertEqual(
            _resolve_segment(io, "bar", index=0, segments=["bar"]),
            "item-bar",
        )

        # Non-mapping, neither attr nor item -> PathResolutionError via e_item
        class Broken:
            def __getitem__(self, key):
                raise KeyError("boom")

        b = Broken()
        with self.assertRaises(PathResolutionError) as cm:
            _resolve_segment(b, "baz", index=0, segments=["baz"])
        self.assertIsInstance(cm.exception.cause, KeyError)


class TestPathPicker(unittest.TestCase):
    def test_pick_success_and_error(self):
        data = {
            "spec": {
                "containers": [
                    {"name": "c1"},
                    {"name": "c2"},
                ]
            }
        }

        class Root:
            pass

        root = from_root_(Root)

        # spec.containers[1].name
        expr_ok = root.spec.containers[1].name
        picker_ok = path_(expr_ok)
        self.assertIsInstance(picker_ok, PathPicker)
        self.assertEqual(picker_ok.pick_(data), "c2")

        # spec.missing -> should raise PathResolutionError
        expr_bad = root.spec.missing
        picker_bad = path_(expr_bad)
        with self.assertRaises(PathResolutionError):
            picker_bad.pick_(data)

    def test_json_path_pointer_empty_segments_and_str(self):
        class Root:
            pass

        root = from_root_(Root)
        # path_(root) -> PathPicker with empty segments
        picker = path_(root)

        # Empty path -> empty JSON Pointer
        self.assertEqual(picker.json_path_pointer(), "")
        # __str__ just delegates to json_path_pointer
        self.assertEqual(str(picker), "")

    def test_json_path_pointer_mixed_segments_and_str(self):
        class Root:
            pass

        root = from_root_(Root)
        # spec.containers[0].env["FOO/BAR~baz"]
        expr = root.spec.containers[0].env["FOO/BAR~baz"]
        picker = path_(expr)

        ptr = picker.json_path_pointer()

        expected = "/" + "/".join(
            [
                escape_json_path_pointer_token("spec"),
                escape_json_path_pointer_token("containers"),
                "0",
                escape_json_path_pointer_token("env"),
                escape_json_path_pointer_token("FOO/BAR~baz"),
            ]
        )

        # Ensure both branches (int vs str) and escaping are exercised
        self.assertEqual(ptr, expected)

        # __str__ must return exactly the same value
        self.assertEqual(str(picker), ptr)


class TestFromRootAndPath(unittest.TestCase):
    def test_from_root_and_path_integration(self):
        class Dummy:
            def __init__(self, value):
                self.spec = {"containers": [{"name": value}]}

        root = from_root_(Dummy)
        expr = root.spec.containers[0].name
        picker = path_(expr)
        self.assertIsInstance(picker, PathPicker)

        dummy = Dummy("val")
        self.assertEqual(picker.pick_(dummy), "val")

    def test_path_with_custom_segments_object(self):
        # Custom object mimicking PathRoot by exposing _segments
        class CustomExpr:
            def __init__(self):
                self._segments = ["x", 1, "y"]

        expr = CustomExpr()
        picker = path_(expr)
        self.assertEqual(picker.segments, ["x", 1, "y"])
