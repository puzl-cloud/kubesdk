from unittest import TestCase
from typing import Type, cast

from kube_models import get_k8s_resource_model
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kube_models.api_v1.io.k8s.api.core.v1 import Secret


class UtilsTest(TestCase):
    def test_model_by_kind(self):
        secret = cast(Type[Secret], get_k8s_resource_model('v1', 'Secret'))
        s = secret(metadata=ObjectMeta(name="some-secret", namespace="default"))
        print(s)
