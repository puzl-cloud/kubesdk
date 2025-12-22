import copy
from unittest import TestCase
from typing import Type, cast
from dataclasses import dataclass, field

from kube_models import get_k8s_resource_model, K8sResource, Loadable
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kube_models.api_v1.io.k8s.api.core.v1 import Secret, Namespace
from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import Deployment


class UtilsTest(TestCase):
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
