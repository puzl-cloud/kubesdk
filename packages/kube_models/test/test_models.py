from unittest import TestCase
from typing import Type, cast
import copy

from kube_models import get_k8s_resource_model
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kube_models.api_v1.io.k8s.api.core.v1 import Secret


class UtilsTest(TestCase):
    def test_model_by_kind(self):
        secret = cast(Type[Secret], get_k8s_resource_model('v1', 'Secret'))
        self.assertEqual("v1", secret.apiVersion)
        self.assertEqual("Secret", secret.kind)
        self.assertIsNone(None, secret.group_)
        self.assertEqual("api/v1/namespaces/{namespace}/secrets", secret.api_path_)
        self.assertEqual("secrets", secret.plural_)

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
