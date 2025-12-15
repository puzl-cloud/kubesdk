from __future__ import annotations

from dataclasses import dataclass
import unittest

from kubesdk._path.picker import PathPicker, PathResolutionError, path_, from_root_
from kubesdk._path.replace_at_path import replace_


@dataclass(frozen=True)
class InnerFrozen:
    x: int
    y: int


@dataclass(frozen=True)
class WithDictFrozen:
    data: dict[str, int]


@dataclass(frozen=True)
class OuterFrozen:
    inner: InnerFrozen


@dataclass
class InnerMutable:
    x: int
    y: int


@dataclass
class OuterMutable:
    inner: InnerMutable


class CustomMapping(dict):
    def __repr__(self):
        return f"CustomMapping({dict(self)!r})"


class ObjWithItemOnly:
    def __init__(self):
        self._data = {"only_item": {"nested": 3}}

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value


class CustomContainer:
    def __init__(self, data):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __repr__(self):
        return f"CustomContainer({self._data!r})"


class ReplaceTests(unittest.TestCase):
    def test_pick_on_deep_dict(self):
        data = {"a": {"b": [1, {"c": 5}]}}
        picker = PathPicker(["a", "b", 1, "c"])
        self.assertEqual(picker.pick_(data), 5)

    def test_replace_on_frozen_dataclasses(self):
        root = OuterFrozen(inner=InnerFrozen(x=1, y=2))
        picker = PathPicker(["inner", "x"])
        new_root = replace_(root, picker, 10)

        self.assertIsInstance(new_root, OuterFrozen)
        self.assertIsNot(root, new_root)
        self.assertEqual(root.inner.x, 1)
        self.assertEqual(new_root.inner.x, 10)
        self.assertEqual(new_root.inner.y, 2)

    def test_replace_on_mutable_dataclasses(self):
        root = OuterMutable(inner=InnerMutable(x=1, y=2))
        picker = PathPicker(["inner", "y"])
        new_root = replace_(root, picker, 20)

        self.assertIsInstance(new_root, OuterMutable)
        self.assertIsNot(root, new_root)
        self.assertEqual(root.inner.y, 2)
        self.assertEqual(new_root.inner.y, 20)
        self.assertEqual(new_root.inner.x, 1)

    def test_replace_in_list_by_index(self):
        root = {"items": [1, 2, 3]}
        picker = PathPicker(["items", 1])
        new_root = replace_(root, picker, 99)

        self.assertEqual(root["items"], [1, 2, 3])
        self.assertEqual(new_root["items"], [1, 99, 3])
        self.assertIsNot(root["items"], new_root["items"])

    def test_replace_in_tuple_by_index(self):
        root = {"items": (1, 2, 3)}
        picker = PathPicker(["items", 2])
        new_root = replace_(root, picker, 42)

        self.assertEqual(root["items"], (1, 2, 3))
        self.assertEqual(new_root["items"], (1, 2, 42))
        self.assertIsInstance(new_root["items"], tuple)

    def test_replace_in_mapping_int_key(self):
        root = {0: {"value": 1}}
        picker = PathPicker([0, "value"])
        new_root = replace_(root, picker, 7)

        self.assertEqual(root[0]["value"], 1)
        self.assertEqual(new_root[0]["value"], 7)
        self.assertIsNot(root, new_root)

    def test_replace_mapping_prefers_key_over_attr(self):
        class HasAttr:
            def __init__(self):
                self.key = {"nested": 1}

        obj = HasAttr()
        mapping = {"key": {"nested": 2}, "obj": obj}
        root = {"wrap": mapping}
        picker = PathPicker(["wrap", "key", "nested"])
        new_root = replace_(root, picker, 3)

        self.assertEqual(root["wrap"]["key"]["nested"], 2)
        self.assertEqual(new_root["wrap"]["key"]["nested"], 3)

    def test_replace_attr_then_dataclass_replace(self):
        root = OuterFrozen(inner=InnerFrozen(x=1, y=2))
        picker = PathPicker(["inner"])
        new_inner = InnerFrozen(x=5, y=6)
        new_root = replace_(root, picker, new_inner)

        self.assertEqual(new_root.inner.x, 5)
        self.assertEqual(new_root.inner.y, 6)

    def test_replace_attr_on_non_dataclass_object(self):
        class Simple:
            def __init__(self, child):
                self.child = child

        root = Simple(child=Simple(child=1))
        picker = PathPicker(["child", "child"])
        new_root = replace_(root, picker, 123)

        self.assertIsInstance(new_root, Simple)
        self.assertIsNot(new_root, root)
        self.assertEqual(new_root.child.child, 123)
        self.assertEqual(root.child.child, 1)

    def test_replace_item_fallback_when_no_attr(self):
        obj = ObjWithItemOnly()
        root = {"obj": obj}
        picker = PathPicker(["obj", "only_item", "nested"])
        new_root = replace_(root, picker, 99)

        # underlying container is mutated in place (best-effort)
        self.assertEqual(root["obj"]._data["only_item"]["nested"], 99)
        self.assertEqual(new_root["obj"]._data["only_item"]["nested"], 99)

    def test_replace_item_when_attr_missing_but_mapping(self):
        mapping = {"only_key": {"nested": 1}}
        root = {"wrap": mapping}
        picker = PathPicker(["wrap", "only_key", "nested"])
        new_root = replace_(root, picker, 10)

        self.assertEqual(root["wrap"]["only_key"]["nested"], 1)
        self.assertEqual(new_root["wrap"]["only_key"]["nested"], 10)

    def test_replace_with_custom_mapping_type_copy(self):
        m = CustomMapping(key={"nested": 1})
        root = {"wrap": m}
        picker = PathPicker(["wrap", "key", "nested"])
        new_root = replace_(root, picker, 11)

        self.assertEqual(new_root["wrap"]["key"]["nested"], 11)

    def test_replace_on_custom_container_int_segment(self):
        container = CustomContainer({0: {"nested": 1}})
        root = {"wrap": container}
        picker = PathPicker(["wrap", 0, "nested"])
        new_root = replace_(root, picker, 55)

        self.assertEqual(root["wrap"]._data[0]["nested"], 55)
        self.assertIs(root["wrap"], new_root["wrap"])

    def test_error_on_invalid_list_index(self):
        root = {"items": [1, 2, 3]}
        picker = PathPicker(["items", 5])
        with self.assertRaises(PathResolutionError):
            replace_(root, picker, 99)

    def test_error_on_missing_mapping_key(self):
        root = {"items": {"a": 1}}
        picker = PathPicker(["items", "b"])
        with self.assertRaises(PathResolutionError):
            replace_(root, picker, 2)

    def test_error_on_missing_attr_and_item(self):
        class NoAttrNoItem:
            pass

        root = {"obj": NoAttrNoItem()}
        picker = PathPicker(["obj", "missing"])
        with self.assertRaises(PathResolutionError):
            replace_(root, picker, 1)

    def test_mapping_under_dataclass_is_copied(self):
        root = WithDictFrozen(data={"x": 1, "y": 2})
        picker = PathPicker(["data", "x"])
        new_root = replace_(root, picker, 99)

        self.assertIsNot(root, new_root)
        self.assertIsNot(root.data, new_root.data)
        self.assertEqual(root.data["x"], 1)
        self.assertEqual(new_root.data["x"], 99)

    def test_attr_clone_without_dict(self):
        class SlotObj:
            __slots__ = ("value",)

            def __init__(self, value):
                self.value = value

        root = SlotObj(1)
        picker = PathPicker(["value"])
        new_root = replace_(root, picker, 2)
        self.assertIs(root, new_root)
        self.assertEqual(root.value, 2)

    def test_attr_clone_broken_new_falls_back_to_current(self):
        class BrokenNew:
            def __new__(cls, *args, **kwargs):
                if args or kwargs:
                    return super().__new__(cls)
                raise RuntimeError("bare __new__ not allowed")

            def __init__(self, value):
                self.value = value

        root = BrokenNew(1)
        picker = PathPicker(["value"])
        new_root = replace_(root, picker, 5)
        self.assertIs(root, new_root)
        self.assertEqual(root.value, 5)

    def test_mapping_item_fallback_when_contains_lies(self):
        from collections.abc import MutableMapping

        class WeirdMapping(MutableMapping):
            def __init__(self):
                self.store = {"key": {"nested": 1}}

            def __getitem__(self, k):
                return self.store[k]

            def __setitem__(self, k, v):
                self.store[k] = v

            def __delitem__(self, k):
                del self.store[k]

            def __iter__(self):
                return iter(self.store)

            def __len__(self):
                return len(self.store)

            def __contains__(self, k):
                # Lie: always False so _resolve_segment uses __getitem__,
                # but replace_ later thinks the key is "missing" and goes
                # through the mapping fallback branch.
                return False

        wm = WeirdMapping()
        root = {"wrap": wm}
        picker = PathPicker(["wrap", "key", "nested"])
        new_root = replace_(root, picker, 10)

        # Root dict is copied
        self.assertIsNot(root, new_root)
        # Underlying WeirdMapping.store is untouched
        self.assertEqual(wm.store["key"]["nested"], 1)
        # New root has updated nested value in the copied mapping
        self.assertEqual(new_root["wrap"]["key"]["nested"], 10)

    def test_replace_on_deep_nested_frozen_dataclasses(self):
        @dataclass(frozen=True)
        class Leaf:
            value: int

        @dataclass(frozen=True)
        class Node:
            name: str
            items: list[Leaf]

        @dataclass(frozen=True)
        class Root:
            nodes: dict[str, Node]

        root_obj = Root(
            nodes={
                "left": Node(name="L", items=[Leaf(1), Leaf(2)]),
                "right": Node(name="R", items=[Leaf(3), Leaf(4)]),
            }
        )
        picker = path_(from_root_(Root).nodes["left"].items[1].value)
        new_root = replace_(root_obj, picker, 99)

        # Root and its mapping are copied
        self.assertIsInstance(new_root, Root)
        self.assertIsNot(root_obj, new_root)
        self.assertIsNot(root_obj.nodes, new_root.nodes)

        # Left branch node and its items list should be copied
        self.assertIsNot(root_obj.nodes["left"], new_root.nodes["left"])
        self.assertIsNot(root_obj.nodes["left"].items, new_root.nodes["left"].items)

        # The targeted deep leaf is updated only in the new structure
        self.assertEqual(root_obj.nodes["left"].items[1].value, 2)
        self.assertEqual(new_root.nodes["left"].items[1].value, 99)

        # Other leaves remain unchanged
        self.assertEqual(new_root.nodes["left"].items[0].value, 1)
        self.assertEqual(new_root.nodes["right"].items[0].value, 3)
        self.assertEqual(new_root.nodes["right"].items[1].value, 4)

        # Right branch is reused (not cloned)
        self.assertIs(new_root.nodes["right"], root_obj.nodes["right"])
