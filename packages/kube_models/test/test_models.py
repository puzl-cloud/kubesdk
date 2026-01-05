import copy
from unittest import TestCase
from typing import Type, cast
from dataclasses import dataclass, field

from kube_models import get_k8s_resource_model, K8sResource, Loadable
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kube_models.api_v1.io.k8s.api.core.v1 import Secret, Namespace
from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import Deployment


class K8sResourceModelsTests(TestCase):
    def test_model_by_kind_core(self):
        secret = cast(Type[Secret], get_k8s_resource_model('v1', 'Secret'))
        self.assertEqual("v1", secret.apiVersion)
        self.assertEqual("Secret", secret.kind)
        self.assertIsNone(None, secret.group_)
        self.assertEqual("api/v1/namespaces/{namespace}/secrets", secret.api_path())
        self.assertEqual("secrets", secret.plural_)
        self.assertEqual(True, Secret.is_namespaced_)
        self.assertEqual(False, Namespace.is_namespaced_)

    def test_model_by_kind_apis_group(self):
        deployment = cast(Type[Deployment], get_k8s_resource_model("apps/v1", "Deployment"))
        self.assertEqual("apps/v1", deployment.apiVersion)
        self.assertEqual("Deployment", deployment.kind)
        self.assertEqual("apps", deployment.group_)
        self.assertEqual("apis/apps/v1/namespaces/{namespace}/deployments", deployment.api_path())
        self.assertEqual("deployments", deployment.plural_)
        self.assertEqual(True, Deployment.is_namespaced_)

    def test_defaults(self):
        self.assertNotEqual(None, K8sResource.patch_strategies_)

    def test_loading(self):
        secret_instance = Secret(
            metadata=ObjectMeta(name="some-secret", namespace="default"),
            data={"key": "value"}
        )
        self.assertEqual("v1", secret_instance.apiVersion)
        self.assertEqual("Secret", secret_instance.kind)

        dumped_secret = secret_instance.to_dict()
        self.assertEqual("v1", dumped_secret["apiVersion"])
        self.assertEqual("Secret", dumped_secret["kind"])

        loaded_secret = Secret.from_dict(dumped_secret)
        self.assertEqual(secret_instance, loaded_secret)

        copied_secret = copy.deepcopy(loaded_secret)
        self.assertEqual(secret_instance, copied_secret)

    def test_custom_resource_load(self):
        @dataclass(kw_only=True, frozen=True, slots=True)
        class NestedField(Loadable):
            value: str

        @dataclass(kw_only=True, frozen=True, slots=True)
        class MyCustomResourceSpec(Loadable):
            some_field: list[NestedField]

        @dataclass(kw_only=True, frozen=True, slots=True)
        class MyCustomResource(K8sResource):
            kind = "MyCustomResource"
            apiVersion = "my-api.com/v1alpha1"
            spec: MyCustomResourceSpec
            status: dict = field(default_factory=dict)
            is_namespaced_ = False
            group_ = "my-api.com"
            plural_ = "mycustomresources"

        resource_src = {
            "kind": "MyCustomResource",
            "apiVersion": "my-api.com/v1alpha1",
            "metadata": {"name": "test"},
            "spec": {"some_field": [{"value": "1"}, {"value": "2"}]}
        }
        res = MyCustomResource.from_dict(resource_src)
        self.assertEqual("2", res.spec.some_field[1].value)


import unittest

from kube_models.apis_apiextensions_k8s_io_v1.io.k8s.apiextensions_apiserver.pkg.apis.apiextensions.v1 import (
    JSONSchemaProps,
)


class TestJSONSchemaPropsLoadDump(unittest.TestCase):
    def test_json_schema_props_load_dump_with_original_names(self):
        src = {
            "$ref": "#/definitions/Foo",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "description": "root schema",
            "not": {"type": "string"},
            "properties": {
                "name": {"type": "string"},
            },
            "x-kubernetes-preserve-unknown-fields": True,
            "x-kubernetes-map-type": "atomic",
            "x-kubernetes-list-map-keys": ["name"],
            "x-kubernetes-list-type": "map",
        }

        obj = JSONSchemaProps.from_dict(src)

        self.assertEqual("#/definitions/Foo", obj.field_ref)
        self.assertEqual("http://json-schema.org/draft-07/schema#", obj.field_schema)
        self.assertIsNotNone(obj.not_)
        self.assertEqual("string", obj.not_.type)

        self.assertIsNotNone(obj.properties)
        self.assertIn("name", obj.properties)
        self.assertEqual("string", obj.properties["name"].type)

        self.assertTrue(obj.x_kubernetes_preserve_unknown_fields)
        self.assertEqual("atomic", obj.x_kubernetes_map_type)
        self.assertEqual(["name"], obj.x_kubernetes_list_map_keys)
        self.assertEqual("map", obj.x_kubernetes_list_type)

        dumped = obj.to_dict()

        # Must dump using original names
        self.assertIn("$ref", dumped)
        self.assertIn("$schema", dumped)
        self.assertIn("not", dumped)
        self.assertIn("x-kubernetes-preserve-unknown-fields", dumped)
        self.assertIn("x-kubernetes-map-type", dumped)
        self.assertIn("x-kubernetes-list-map-keys", dumped)
        self.assertIn("x-kubernetes-list-type", dumped)

        self.assertNotIn("field_ref", dumped)
        self.assertNotIn("field_schema", dumped)
        self.assertNotIn("not_", dumped)
        self.assertNotIn("x_kubernetes_preserve_unknown_fields", dumped)

        def compact(x):
            if x is None:
                return None
            if isinstance(x, dict):
                out = {}
                for k, v in x.items():
                    v2 = compact(v)
                    if v2 is None or v2 == {} or v2 == []:
                        continue
                    out[k] = v2
                return out
            if isinstance(x, list):
                out = []
                for v in x:
                    v2 = compact(v)
                    if v2 is None or v2 == {} or v2 == []:
                        continue
                    out.append(v2)
                return out
            return x

        self.assertEqual(src, compact(dumped))
