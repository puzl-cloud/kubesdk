from __future__ import annotations

import unittest
import pkgutil
import importlib
from dataclasses import dataclass, field

import kube_models
from kube_models.registry import ALL_RESOURCES, register_model, maybe_get_model_key, get_model_by_body, get_model


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        ALL_RESOURCES.clear()

    def test_key_from_class_attributes_and_basic_lookup(self) -> None:
        class ModelWithAttributes:
            apiVersion = "v1"
            kind = "Secret"

        self.assertEqual(("v1", "Secret"), maybe_get_model_key(ModelWithAttributes))

        register_model(ModelWithAttributes)
        self.assertIs(ModelWithAttributes, get_model("v1", "Secret"))
        self.assertIsNone(get_model("v1", "DoesNotExist"))

    def test_key_from_slots_dataclass_default_values(self) -> None:
        @dataclass(slots=True, frozen=True)
        class SlotsDataclassDefaults:
            apiVersion: str = "apps/v1"
            kind: str = "Deployment"
        self.assertEqual(("apps/v1", "Deployment"), maybe_get_model_key(SlotsDataclassDefaults))
        register_model(SlotsDataclassDefaults)
        self.assertIs(SlotsDataclassDefaults, get_model("apps/v1", "Deployment"))

    def test_key_from_slots_dataclass_default_factory(self) -> None:
        @dataclass(slots=True, frozen=True)
        class SlotsDataclassFactories:
            apiVersion: str = field(default_factory=lambda: "batch/v1")
            kind: str = field(default_factory=lambda: "Job")

        self.assertEqual(("batch/v1", "Job"), maybe_get_model_key(SlotsDataclassFactories))

    def test_dataclass_fields_missing_one_side(self) -> None:
        @dataclass(slots=True, frozen=True)
        class OnlyKind:
            kind: str = "Pod"

        # apiVersion field is missing -> covers field_object is None branch
        self.assertIsNone(maybe_get_model_key(OnlyKind))

    def test_dataclass_fields_present_but_not_dict(self) -> None:
        class NotADataclass:
            __dataclass_fields__ = "not-a-dict"

        self.assertIsNone(maybe_get_model_key(NotADataclass))

    def test_dataclass_fields_no_string_defaults(self) -> None:
        @dataclass(slots=True, frozen=True)
        class NoDefaults:
            apiVersion: str
            kind: str

        self.assertIsNone(maybe_get_model_key(NoDefaults))

        @dataclass(slots=True, frozen=True)
        class NonStringDefaults:
            apiVersion: int = 1  # not a string
            kind: str = "Thing"

        self.assertIsNone(maybe_get_model_key(NonStringDefaults))

    def test_register_model_does_not_override_existing(self) -> None:
        class FirstRegistered:
            apiVersion = "v1"
            kind = "ConfigMap"

        class SecondRegistered:
            apiVersion = "v1"
            kind = "ConfigMap"

        register_model(FirstRegistered)
        register_model(SecondRegistered)
        self.assertIs(FirstRegistered, get_model("v1", "ConfigMap"))

    def test_get_model_by_body_validation(self) -> None:
        class BodyModel:
            apiVersion = "v1"
            kind = "Service"

        register_model(BodyModel)

        self.assertIsNone(get_model_by_body({}))
        self.assertIsNone(get_model_by_body({"apiVersion": "v1", "kind": None}))
        self.assertIsNone(get_model_by_body({"apiVersion": None, "kind": "Service"}))

        self.assertIs(BodyModel, get_model_by_body({"apiVersion": "v1", "kind": "Service"}))

    def test_import_all(self):
        initial_len = len(ALL_RESOURCES)
        pkg = kube_models
        prefix = pkg.__name__ + "."
        for _finder, modname, _ispkg in pkgutil.walk_packages(pkg.__path__, prefix=prefix):
            # Skip private files/modules
            if modname.rsplit(".", 1)[-1].startswith("_"):
                continue

            importlib.import_module(modname)

        final_len = len(ALL_RESOURCES)
        self.assertGreater(final_len, initial_len)
