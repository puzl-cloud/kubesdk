# test_jsonpatch_diff.py

import unittest
import json
from kubesdk._patch.json_patch import json_patch_from_diff, apply_patch, escape_json_path_pointer_token, \
    _parse_pointer, JsonPointerError, _is_scalar, _deep_equal, _join_path, JsonPatchTestFailed

class TestJsonPatchDiff(unittest.TestCase):
    def assertPatchTransforms(self, old, new):
        patch = json_patch_from_diff(old, new)
        result = apply_patch(old, patch)
        self.assertEqual(result, new, f"Patch failed. Patch: {json.dumps(patch, indent=2)}")

    def test_scalars_replace(self):
        self.assertPatchTransforms(1, 2)
        self.assertPatchTransforms("a", "b")
        self.assertPatchTransforms(True, False)
        self.assertPatchTransforms(None, 0)

    def test_dict_add_remove_replace(self):
        old = {"a": 1, "b": 2}
        new = {"b": 3, "c": 4}
        self.assertPatchTransforms(old, new)

    def test_nested_dicts(self):
        old = {"a": {"x": 1, "y": 2}, "b": {"z": 3}}
        new = {"a": {"x": 1, "y": 99}, "b": {"z": 3, "t": 4}}
        self.assertPatchTransforms(old, new)

    def test_list_simple(self):
        old = [1,2,3,4]
        new = [1,99,3,4,5]
        self.assertPatchTransforms(old, new)

    def test_list_replacements(self):
        old = [{"k":1}, {"k":2}, {"k":3}]
        new = [{"k":1}, {"k":20}, {"k":30}]
        self.assertPatchTransforms(old, new)

    def test_list_insert_delete(self):
        old = ["a","b","c","d"]
        new = ["a","c","e"]
        self.assertPatchTransforms(old, new)

    def test_type_change_at_root(self):
        old = {"a": 1}
        new = [ {"a": 1} ]
        self.assertPatchTransforms(old, new)

    def test_mixed_complex(self):
        old = {
            "name": "doc",
            "tags": ["x","y","z"],
            "meta": {"a":1,"nested":{"v": [1,2,3]}},
        }
        new = {
            "name": "doc2",
            "tags": ["x","z","w"],
            "meta": {"a":2,"nested":{"v": [1,3,4], "extra": True}},
        }
        self.assertPatchTransforms(old, new)

    def test_pointer_escaping(self):
        old = {"a/b": {"t~n": 1}}
        new = {"a/b": {"t~n": 2}, "plain": 0}
        self.assertPatchTransforms(old, new)
        # direct escape function checks
        self.assertEqual(escape_json_path_pointer_token("a/b"), "a~1b")
        self.assertEqual(escape_json_path_pointer_token("t~n"), "t~0n")

    def test_remove_errors(self):
        # Removing root should raise
        with self.assertRaises(JsonPointerError):
            apply_patch({"a":1}, [{"op":"remove","path":"/"}])

    def test_parse_pointer(self):
        is_root, tokens = _parse_pointer("/")
        self.assertTrue(is_root)
        self.assertEqual(tokens, [])

        is_root, tokens = _parse_pointer("/a/~0/~1/3")
        self.assertFalse(is_root)
        self.assertEqual(tokens, ["a","~","/", "3"])

    def test_idempotence(self):
        # applying diff twice shouldn't change the second time (patch of equal docs is empty)
        doc = {"a":[1,2,3],"b":{"c":1}}
        patch = json_patch_from_diff(doc, doc)
        self.assertEqual(patch, [])
        after = apply_patch(doc, patch)
        self.assertEqual(after, doc)

    def test_edge_array_to_scalar(self):
        old = {"a":[1,2,3]}
        new = {"a":"str"}
        self.assertPatchTransforms(old, new)

    def test_edge_scalar_to_object(self):
        old = "x"
        new = {"x": 1}
        self.assertPatchTransforms(old, new)

    # ---- Extra coverage tests below ----

    def test_parse_pointer_invalid(self):
        with self.assertRaises(JsonPointerError):
            _parse_pointer("")  # empty
        with self.assertRaises(JsonPointerError):
            _parse_pointer("no-slash")  # invalid

    def test_traverse_into_scalar_error(self):
        with self.assertRaises(JsonPointerError):
            apply_patch({"a":1}, [{"op":"add","path":"/a/b","value":2}])

    def test_add_root_operation(self):
        # ensure add at root replaces the whole document
        new = {"x": 42}
        res = apply_patch({"a":1}, [{"op":"add","path":"/","value":new}])
        self.assertEqual(res, new)

    def test_remove_missing_key(self):
        # removing non-existent key should be a no-op
        res = apply_patch({"a":1}, [{"op":"remove","path":"/b"}])
        self.assertEqual(res, {"a":1})

    def test_list_insert_middle(self):
        res = apply_patch([1,3], [{"op":"add","path":"/1","value":2}])
        self.assertEqual(res, [1,2,3])

    def test_join_path_variants(self):
        self.assertEqual(_join_path("", "a"), "/a")
        self.assertEqual(_join_path("/", "a"), "/a")
        self.assertEqual(_join_path("/base", "a/b"), "/base/a~1b")

    def test_invalid_targets_on_scalar_parent(self):
        # add into scalar parent
        with self.assertRaises(JsonPointerError):
            apply_patch(1, [{"op":"add","path":"/0","value":123}])
        # replace into scalar parent
        with self.assertRaises(JsonPointerError):
            apply_patch(1, [{"op":"replace","path":"/0","value":123}])
        # remove into scalar parent
        with self.assertRaises(JsonPointerError):
            apply_patch(1, [{"op":"remove","path":"/0"}])

    def test_is_scalar_and_deep_equal_exception(self):
        self.assertTrue(_is_scalar(1))
        self.assertTrue(_is_scalar("x"))
        self.assertTrue(_is_scalar(False))
        self.assertTrue(_is_scalar(None))
        self.assertFalse(_is_scalar([]))
        self.assertFalse(_is_scalar({}))

        class BadEq:
            def __init__(self, v): self.v = v
            def __eq__(self, other):
                raise RuntimeError("boom")

        a, b = BadEq(1), BadEq(2)
        # _deep_equal should handle exception and return False
        self.assertFalse(_deep_equal(a, b))
        # This should generate a replace op at root; apply and compare fields (avoid __eq__)
        patch = json_patch_from_diff(a, b)
        res = apply_patch(a, patch)
        self.assertEqual(res.__class__, b.__class__)
        self.assertEqual(getattr(res, 'v', None), getattr(b, 'v', None))

    def test_add_root(self):
        doc = {"a": 1}
        patch = [{"op": "add", "path": "/", "value": {"x": 2}}]
        self.assertEqual(apply_patch(doc, patch), {"x": 2})

    def test_add_dict_key(self):
        doc = {"a": 1}
        patch = [{"op": "add", "path": "/b", "value": 2}]
        self.assertEqual(apply_patch(doc, patch), {"a": 1, "b": 2})

    def test_add_list_index_and_append(self):
        doc = {"arr": [1, 3]}
        patch = [
            {"op": "add", "path": "/arr/1", "value": 2},
            {"op": "add", "path": "/arr/-", "value": 4},
        ]
        self.assertEqual(apply_patch(doc, patch), {"arr": [1, 2, 3, 4]})

    def test_remove_root_error(self):
        doc = {"a": 1}
        patch = [{"op": "remove", "path": "/"}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)

    def test_remove_dict_key_and_list_index(self):
        doc = {"a": 1, "b": [9, 8, 7]}
        patch = [{"op": "remove", "path": "/a"}, {"op": "remove", "path": "/b/1"}]
        self.assertEqual(apply_patch(doc, patch), {"b": [9, 7]})

    def test_remove_dash_invalid(self):
        doc = {"a": [1]}
        patch = [{"op": "remove", "path": "/a/-"}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)

    def test_replace_root_and_in_list(self):
        doc = {"a": [1, 2, 3]}
        patch = [{"op": "replace", "path": "/", "value": {"a": [1, 9, 3]}}]
        self.assertEqual(apply_patch(doc, patch), {"a": [1, 9, 3]})
        # Now replace in list
        doc2 = {"a": [1, 2, 3]}
        patch2 = [{"op": "replace", "path": "/a/1", "value": 42}]
        self.assertEqual(apply_patch(doc2, patch2), {"a": [1, 42, 3]})

    def test_replace_dash_invalid(self):
        doc = {"a": [1]}
        patch = [{"op": "replace", "path": "/a/-", "value": 99}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)

    def test_copy_root_to_key_and_list_positions(self):
        doc = {"a": {"x": 1}, "list": [10]}
        patch = [
            {"op": "copy", "path": "/b", "from": "/a"},
            {"op": "copy", "path": "/list/0", "from": "/a/x"},
            {"op": "copy", "path": "/list/-", "from": "/a/x"},
        ]
        res = apply_patch(doc, patch)
        self.assertEqual(res["b"], {"x": 1})
        self.assertEqual(res["list"], [1, 10, 1])

    def test_move_within_same_list_forward_and_backward(self):
        # forward move: index 1 -> index 3 (after element), adjustment needed
        doc = {"a": [0, 1, 2, 3]}
        patch = [{"op": "move", "from": "/a/1", "path": "/a/3"}]
        self.assertEqual(apply_patch(doc, patch), {"a": [0, 2, 1, 3]})
        # backward move: index 3 -> index 1
        doc2 = {"a": [0, 1, 2, 3]}
        patch2 = [{"op": "move", "from": "/a/3", "path": "/a/1"}]
        self.assertEqual(apply_patch(doc2, patch2), {"a": [0, 3, 1, 2]})

    def test_move_root_error(self):
        doc = {"x": 1}
        patch = [{"op": "move", "from": "/", "path": "/y"}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)

    def test_move_to_root(self):
        doc = {"a": {"x": 1}}
        patch = [{"op": "move", "from": "/a", "path": "/"}]
        self.assertEqual(apply_patch(doc, patch), {"x": 1})

    def test_copy_invalid_index_raises(self):
        doc = {"a": []}
        patch = [{"op": "copy", "path": "/a/x", "from": "/a"}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)

    def test_test_op_success_and_failure(self):
        doc = {"a": {"x": 1}, "b": [1, 2, 3]}
        ok = [
            {"op": "test", "path": "/a/x", "value": 1},
            {"op": "test", "path": "/b/1", "value": 2},
            {"op": "test", "path": "/", "value": {"a": {"x": 1}, "b": [1, 2, 3]}},
        ]
        self.assertEqual(apply_patch(doc, ok), doc)
        bad = [{"op": "test", "path": "/a/x", "value": 2}]
        with self.assertRaises(JsonPatchTestFailed):
            apply_patch(doc, bad)

    def test_unsupported_op(self):
        doc = {}
        patch = [{"op": "unknown", "path": "/"}]
        with self.assertRaises(NotImplementedError):
            apply_patch(doc, patch)

    #
    # Test invalid cases
    #
    def test_invalid_add_target(self):
        doc = {"x": 5}
        patch = [{"op": "add", "path": "/x/0", "value": 1}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid add target", str(cm.exception))

    def test_invalid_replace_target(self):
        doc = {"x": 5}
        patch = [{"op": "replace", "path": "/x/0", "value": 1}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid replace target", str(cm.exception))

    def test_invalid_remove_target(self):
        doc = {"x": 5}
        patch = [{"op": "remove", "path": "/x/0"}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid remove target", str(cm.exception))

    def test_invalid_copy_target(self):
        doc = {"x": 5, "a": {"v": 1}}
        patch = [{"op": "copy", "from": "/a/v", "path": "/x/0"}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid copy target", str(cm.exception))

    def test_invalid_move_source(self):
        # parent of 'from' is scalar: root is scalar with single-segment from path
        doc = 0
        patch = [{"op": "move", "from": "/0", "path": "/"}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid move source", str(cm.exception))

    def test_invalid_move_target(self):
        # destination parent is scalar
        doc = {"x": 5, "a": [1]}
        patch = [{"op": "move", "from": "/a/0", "path": "/x/0"}]
        with self.assertRaises(JsonPointerError) as cm:
            apply_patch(doc, patch)
        self.assertIn("Invalid move target", str(cm.exception))

    def test_invalid_array_index_add_remove_replace(self):
        doc = {"a": [1]}
        # add with non-integer index
        with self.assertRaises(JsonPointerError) as cm1:
            apply_patch(doc, [{"op": "add", "path": "/a/x", "value": 2}])
        self.assertIn("Invalid array index", str(cm1.exception))
        # remove with non-integer index
        with self.assertRaises(JsonPointerError) as cm2:
            apply_patch(doc, [{"op": "remove", "path": "/a/x"}])
        self.assertIn("Invalid array index", str(cm2.exception))
        # replace with non-integer index
        with self.assertRaises(JsonPointerError) as cm3:
            apply_patch(doc, [{"op": "replace", "path": "/a/x", "value": 3}])
        self.assertIn("Invalid array index", str(cm3.exception))

    def test_get_at_pointer_dash_invalid_in_test(self):
        doc = {"a": [1]}
        patch = [{"op": "test", "path": "/a/-", "value": 1}]
        with self.assertRaises(JsonPointerError):
            apply_patch(doc, patch)
