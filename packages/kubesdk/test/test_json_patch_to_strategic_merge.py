import unittest
import json
from typing import ClassVar
from types import SimpleNamespace
from dataclasses import dataclass, field

import jsonpatch

from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import Deployment, DeploymentSpec, DeploymentStrategy, ObjectMeta, \
    PodTemplateSpec, RollingUpdateDeployment, LabelSelector
from kube_models.api_v1.io.k8s.api.core.v1 import Container, PodSpec, EnvVar
from kube_models.resource import K8sResource
from kube_models.const import *

from kubesdk._patch.strategic_merge_patch import jsonpatch_to_smp, _is_int_token, _list_diff, \
    _lookup_merge_info_by_type, _remove_by_pointer, _get_by_pointer, _set_by_pointer, _split_pointer
from kubesdk._patch.json_patch import JsonPatchTestFailed


def apply_smp(original: dict, patch: dict, root: K8sResource):
    """
    Apply a Strategic Merge Patch 'patch' to 'original' using minimal SMP semantics:
      - $patch/<field>: "replace"
      - $setElementOrder/<field>: [...]
      - $retainKeys: ['k1', 'k2', ...]  (remove all non-retained keys at this object level)
      - $deleteFromPrimitiveList/<field>: [values...]  (remove listed primitives from a list)
      - keyed list merge for fields with patch-merge-key
      - plain merge for dicts / replace for unkeyed lists

    Notes:
      * This is a pragmatic applier for tests implementing this spec https://github.com/kubernetes/community/blob/master/contributors/devel/sig-api-machinery/strategic-merge-patch.md
      * Unknown directives are ignored.
    """
    doc = json.loads(json.dumps(original))

    def merge(node, p, path=()):
        # Directives (field-level)
        for key in list(p.keys()):
            # Replace the whole field with the value mirrored under the same name
            if key.startswith("$patch/") and p[key] == "replace":
                field = key.split("/", 1)[1]
                node[field] = json.loads(json.dumps(p.get(field)))

            # Reorder keyed lists
            if key.startswith("$setElementOrder/"):
                field = key.split("/", 1)[1]
                desired = p[key]
                merge_info = _lookup_merge_info_by_type(path + (field,), root)
                if isinstance(node.get(field), list) and desired:
                    mkey = (merge_info or {}).get(PATCH_MERGE_KEY)
                    if mkey:
                        current = {
                            it[mkey]: it
                            for it in node[field]
                            if isinstance(it, dict) and mkey in it
                        }
                        ordered = [
                            current[d[mkey]]
                            for d in desired
                            if isinstance(d, dict) and d.get(mkey) in current
                        ]
                        desired_keys = {
                            d[mkey] for d in desired
                            if isinstance(d, dict) and mkey in d
                        }
                        tail = [
                            x for x in node[field]
                            if isinstance(x, dict)
                            and mkey in x
                            and x[mkey] not in desired_keys
                        ]
                        node[field] = ordered + tail

            # Delete from primitive list
            if key.startswith("$deleteFromPrimitiveList/"):
                field = key.split("/", 1)[1]
                to_delete = p[key] or []
                if isinstance(node.get(field), list):
                    node[field] = [v for v in node[field] if v not in to_delete]

        # Object-level directive: retainKeys
        if "$retainKeys" in p and isinstance(node, dict):
            retain = set(p["$retainKeys"] or [])
            # Remove any non-retained, non-directive keys at this level
            for k in list(node.keys()):
                if k not in retain and not k.startswith("$"):
                    node.pop(k, None)

        # Recursive merge for regular entries
        for k, v in p.items():
            # skip directives; they’re already handled above
            if (
                k.startswith("$patch/")
                or k.startswith("$setElementOrder/")
                or k.startswith("$deleteFromPrimitiveList/")
                or k == "$retainKeys"
            ):
                continue

            if v is None:
                node.pop(k, None)
            elif isinstance(v, dict):
                node[k] = merge(node.get(k, {}), v, path + (k,))
            elif isinstance(v, list):
                # keyed merge vs replace
                merge_info = _lookup_merge_info_by_type(path + (k,), root)
                if merge_info and merge_info.get(PATCH_MERGE_KEY):
                    mkey = merge_info[PATCH_MERGE_KEY]
                    cmap = {
                        it[mkey]: json.loads(json.dumps(it))
                        for it in node.get(k, [])
                        if isinstance(it, dict) and mkey in it
                    }
                    for entry in v:
                        if isinstance(entry, dict) and entry.get("$patch") == "delete":
                            nm = entry.get(mkey)
                            if nm in cmap:
                                del cmap[nm]
                        elif isinstance(entry, dict) and mkey in entry:
                            nm = entry[mkey]
                            base = cmap.get(nm, {})
                            cmap[nm] = merge(base, entry, path + (k, mkey, str(nm)))
                    node[k] = list(cmap.values())
                else:
                    # unkeyed list -> replace directly
                    node[k] = json.loads(json.dumps(v))
            else:
                node[k] = json.loads(json.dumps(v))

        return node

    return merge(doc, patch, ())


def _base():
    return Deployment(
        metadata=ObjectMeta(name="demo", labels={"app": "web"}, finalizers=["protect-me", "do-not-delete"]),
        spec=DeploymentSpec(
            replicas=2,
            strategy=DeploymentStrategy(type="RollingUpdate", rollingUpdate=RollingUpdateDeployment(maxUnavailable="25%", maxSurge="25%")),
            template=PodTemplateSpec(
                spec=PodSpec(
                    containers=[
                        Container(name="app", image="app:v1", env=[EnvVar(**{"name": "APP_PORT", "value": "80"})]),
                        Container(name="sidecar", image="busybox:1.36", env=[]),
                    ]
                )
            ),
            selector=LabelSelector(matchLabels={"app": "web"})
        ),
    )

class TestJsonPatchToSMP(unittest.TestCase):
    def test_scalar_replace(self):
        base = _base()
        ops = [{"op": "replace", "path": "/spec/replicas", "value": 5}]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_map_add_remove(self):
        base = _base()
        ops = [
            {"op": "add", "path": "/metadata/labels/tier", "value": "prod"},
            {"op": "remove", "path": "/metadata/labels/app"},
        ]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_keyed_list_update_by_index(self):
        base = _base()
        ops = [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "app:v2"}]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)
        self.assertNotIn("$setElementOrder/containers", json.dumps(smp))

    def test_keyed_list_delete_and_reorder(self):
        base = _base()
        ops = [
            {"op": "remove", "path": "/spec/template/spec/containers/0"},
            {"op": "add", "path": "/spec/template/spec/containers/1", "value": {"name": "app", "image": "app:v1", "env": [{"name": "APP_PORT", "value": "80"}]}},
        ]
        smp = jsonpatch_to_smp(base, ops)
        self.assertIn("$setElementOrder/containers", json.dumps(smp))
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_keyed_list_delete_by_index(self):
        base = _base()
        ops = [{"op": "remove", "path": "/spec/template/spec/containers/1"}]
        smp = jsonpatch_to_smp(base, ops)
        js = json.dumps(smp)
        self.assertNotIn("$patch/containers", js)
        self.assertNotIn("$patch\\u002Fcontainers", js)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_metadata_finalizers_forces_replace(self):
        # reuse an existing Spec from your helper to keep the rest realistic
        base = _base()

        # Change a primitive list (finalizers): remove one, add one at index 0 -> should force $patch: replace
        ops = [
            {"op": "remove", "path": "/metadata/finalizers/1"},
            {"op": "add", "path": "/metadata/finalizers/0", "value": "cleanup"},
        ]

        smp = jsonpatch_to_smp(base, ops)

        js = json.dumps(smp)
        self.assertTrue(("$patch/finalizers" in js) or ("$patch\\u002Ffinalizers" in js))
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_object_replace(self):
        base = _base()
        ops = [{"op": "replace", "path": "/spec/strategy", "value": {"type": "Recreate"}}]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)
        self.assertNotIn("rollingUpdate", json.dumps(after))

    def test_move_and_copy(self):
        base = _base()
        ops = [
            {"op": "move", "from": "/metadata/labels/app", "path": "/metadata/labels/application"},
            {"op": "copy", "from": "/metadata/labels/application", "path": "/metadata/labels/app"},
        ]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)

    def test_test_op(self):
        base = _base()
        ops = [{"op": "test", "path": "/spec/replicas", "value": 2},
               {"op": "replace", "path": "/spec/replicas", "value": 3}]
        smp = jsonpatch_to_smp(base, ops)
        after = apply_smp(base.to_dict(), smp, base)
        expect = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        self.assertEqual(after, expect)
        with self.assertRaises(JsonPatchTestFailed):
            jsonpatch_to_smp(base, [{"op":"test","path":"/spec/replicas","value":999}])

    def test_keyed_list_add_new_item(self):
        base = _base()
        ops = [{"op": "add", "path": "/spec/template/spec/containers/-",
                "value": {"name": "logger", "image": "alpine:3"}}]
        smp = jsonpatch_to_smp(base, ops)

        expected = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        after = apply_smp(base.to_dict(), smp, base)
        self.assertEqual(after, expected)

    def test_unsupported_strategy_raises(self):

        @dataclass(kw_only=True, frozen=True)
        class Fake(K8sResource):
            apiVersion: ClassVar[str] = "apps/v1"
            kind: ClassVar[str] = "FakeKind"
            patch_strategies_: ClassVar[set[str]] = {PatchRequestType.json}
            spec: SimpleNamespace = field(default_factory=SimpleNamespace)

        base = Fake(metadata=ObjectMeta(name="demo"))
        with self.assertRaises(TypeError):
            jsonpatch_to_smp(base, [{"op":"replace","path":"/spec/replicas","value":2}])

    def test__split_pointer_and_unescape(self):
        self.assertEqual(_split_pointer("/a/b~1c/d~0e"), ["a","b/c","d~e"])
        with self.assertRaises(ValueError):
            _split_pointer("not-a-pointer")
        self.assertEqual(_split_pointer("/"), [])

    def test__get_by_pointer(self):
        doc = {"a":[{"b":1}, {"b":2}]}
        self.assertEqual(_get_by_pointer(doc, "/a/1/b"), 2)
        with self.assertRaises(KeyError):
            _get_by_pointer(doc, "/a/-/b")

    def test__set_by_pointer_builds_structs_and_inserts(self):
        doc = {}
        # create x list and first element dict
        _set_by_pointer(doc, "/x/0", {})
        _set_by_pointer(doc, "/x/0/y", 42)
        # insert at index (not append) and at end == append
        _set_by_pointer(doc, "/x/0/z", "hi")  # inside existing dict
        _set_by_pointer(doc, "/x/1", {"y": 99})
        _set_by_pointer(doc, "/x/-", {"y": 100})  # append
        self.assertEqual(doc["x"][0]["z"], "hi")
        self.assertEqual(doc["x"][1]["y"], 99)
        self.assertEqual(doc["x"][2]["y"], 100)
        with self.assertRaises(ValueError):
            _set_by_pointer({}, "/", 1)

    def test__set_by_pointer_insert_middle_of_list(self):
        # cover insert branch where idx < len(list)
        doc = {"x": [1, 3]}
        _set_by_pointer(doc, "/x/1", 2)  # insert at position 1
        self.assertEqual(doc["x"], [1,2,3])

    def test__remove_by_pointer(self):
        doc = {"a":{"b":[0,1,2]}}
        removed = _remove_by_pointer(doc, "/a/b/1")
        self.assertEqual(removed, 1)
        self.assertEqual(doc, {"a":{"b":[0,2]}})
        removed2 = _remove_by_pointer(doc, "/a/b")
        self.assertEqual(removed2, [0,2])
        self.assertEqual(doc, {"a":{}})
        with self.assertRaises(ValueError):
            _remove_by_pointer(doc, "/")

    def test__remove_by_pointer_traverse_through_list_then_field(self):
        # cover remove path where list is not last segment
        doc = {"a": [{"x":1}, {"x":2, "y":3}]}
        removed = _remove_by_pointer(doc, "/a/1/y")
        self.assertEqual(removed, 3)
        self.assertEqual(doc, {"a":[{"x":1},{"x":2}]})

    def test_is_int_token(self):
        self.assertTrue(_is_int_token("12"))
        self.assertFalse(_is_int_token("x12"))

    def test__lookup_merge_info_by_type(self):
        base = _base()
        info = _lookup_merge_info_by_type(("spec","template","spec","containers","0"), base)
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get(PATCH_MERGE_KEY), "name")

    def test_lookup_merge_info_with_dash_token_and_unknown_field(self):
        base = _base()
        # '-' token should be skipped, and unknown field returns last_meta
        info1 = _lookup_merge_info_by_type(("spec","template","spec","containers","-"), base)
        self.assertIsInstance(info1, dict)
        # Move into a known list (containers), then unknown field -> returns last_meta
        info2 = _lookup_merge_info_by_type(("spec","template","spec","containers","0","__unknown__"), base)
        self.assertEqual(info2, info1)

    def test_lookup_merge_info_primitive_descend_returns_last(self):
        base = _base()
        # Descend into a primitive then ask deeper -> should return last_meta
        info = _lookup_merge_info_by_type(("spec","template","spec","containers","0","name","bogus"), base)
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get(PATCH_MERGE_KEY), "name")

    def test_lookup_merge_info_unknown_field_directly_after_list(self):
        base = _base()
        info = _lookup_merge_info_by_type(("spec","template","spec","containers","bogus"), base)
        # Should return last known list metadata (containers)
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get(PATCH_MERGE_KEY), "name")

    def test_lookup_merge_info_primitive_then_bogus(self):
        base = _base()
        # Go into element's 'name' (primitive), then bogus token -> returns last_meta
        info = _lookup_merge_info_by_type(("spec","template","spec","containers","name","bogus"), base)
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get(PATCH_MERGE_KEY), "name")

    def test_list_diff_unkeyed_replace(self):
        # primitive list should trigger replace if changed
        base = _base()
        patch_list, order, repl, prim = _list_diff(["a","b"], ["b","c"], base, ("metadata","finalizers"))
        self.assertEqual(patch_list, ["b","c"])
        self.assertTrue(repl)

    def test_list_diff_keyed_equal_and_unkeyed_equal(self):
        base = _base()
        # keyed equal
        orig = [{"name":"a","image":"i1"},{"name":"b","image":"i2"}]
        target = [{"name":"a","image":"i1"},{"name":"b","image":"i2"}]
        patch_list, order, repl, del_from_prim = _list_diff(orig, target, base, ("spec","template","spec","containers"))
        self.assertIsNone(patch_list)
        self.assertIsNone(order)
        self.assertFalse(repl)
        self.assertFalse(del_from_prim)

        # unkeyed equal (primitive)
        patch_list, order, repl, del_from_prim = _list_diff(["a","b"], ["a","b"], base, ("metadata","finalizers"))
        self.assertIsNone(patch_list)
        self.assertIsNone(order)
        self.assertFalse(repl)
        self.assertIsNone(del_from_prim)

    def test_jsonpatch_to_smp_full_flow_add_and_delete(self):
        base = _base()
        ops = [
            {"op":"add","path":"/spec/template/spec/containers/-","value":{"name":"logger","image":"alpine:3"}},
            {"op":"remove","path":"/metadata/labels/app"},
        ]
        smp = jsonpatch_to_smp(base, ops)
        after = jsonpatch.JsonPatch(ops).apply(base.to_dict())
        after_smp = apply_smp(base.to_dict(), smp, base)
        self.assertEqual(after, after_smp)

    def test_retainkeys_on_strategy_changes(self):
        base = _base()
        ops = [
            {"op": "replace", "path": "/spec/strategy/type", "value": "Recreate"},
            {"op": "remove", "path": "/spec/strategy/rollingUpdate"}
        ]
        smp = jsonpatch_to_smp(base, ops)
        strat = smp.get("spec", {}).get("strategy", {})
        assert "$retainKeys" in strat
        assert strat["$retainKeys"] == ["type"]
        assert strat["type"] == "Recreate"

    def test_deleteFromPrimitiveList_finalizers(self):
        base = _base()
        ops = [{"op": "remove", "path": "/metadata/finalizers/0"}]
        smp = jsonpatch_to_smp(base, ops)
        meta = smp.get("metadata", {})
        assert meta["$deleteFromPrimitiveList/finalizers"] == ["protect-me"]
