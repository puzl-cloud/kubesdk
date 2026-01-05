import unittest
from dataclasses import dataclass, field as dc_field, fields as dc_fields
from typing import Any, Optional, Union, Annotated

from kubesdk.crd import (
    CRDFieldSpec,
    crd_field,
    _schema_from_type,
    _schema_from_dataclass_type,
    _resource_schema,
    _is_optional_type,
    _optional_inner_type,
    _default_column_type,
    _get_crd_field_spec,
    _apply_crd_field_overrides,
    CustomK8sResource,
    CustomK8sResourceDefinition,
    PrinterColumn,
    _collect_printer_columns,
    _recurse_printer_columns
)

from kube_models import K8sResource


class TestFieldSpecAndOverrides(unittest.TestCase):
    def test_get_crd_field_spec_roundtrip(self):
        @dataclass(kw_only=True)
        class Spec:
            a: int = crd_field(spec=CRDFieldSpec(description="x"))

        f = dc_fields(Spec)[0]
        spec = _get_crd_field_spec(f)
        self.assertIsInstance(spec, CRDFieldSpec)
        self.assertEqual("x", spec.description)

    def test_get_crd_field_spec_ignores_wrong_type(self):
        @dataclass(kw_only=True)
        class Spec:
            a: int = dc_field(metadata={"kubesdk_crd_field_spec": "nope"})

        f = dc_fields(Spec)[0]
        self.assertIsNone(_get_crd_field_spec(f))

    def test_apply_overrides_identity_when_no_overrides(self):
        base = _schema_from_type(str)
        merged = _apply_crd_field_overrides(base, CRDFieldSpec())
        self.assertEqual(merged, base)

    def test_crd_field_default_and_factory(self):
        @dataclass(kw_only=True)
        class Spec:
            a: int = crd_field(default=1)
            b: list[int] = crd_field(default_factory=list)

        s = Spec()
        self.assertEqual(1, s.a)
        self.assertEqual([], s.b)


class TestOptionalHelpers(unittest.TestCase):
    def test_is_optional_type(self):
        self.assertTrue(_is_optional_type(int | None))
        self.assertTrue(_is_optional_type(Optional[int]))
        self.assertFalse(_is_optional_type(int | str))

    def test_optional_inner_type_multiple_returns_any(self):
        t = Union[int, str, None]
        self.assertEqual(Any, _optional_inner_type(t))


class TestSchemaGeneration(unittest.TestCase):
    def test_schema_float_is_number(self):
        sch = _schema_from_type(float)
        self.assertEqual("number", sch.type)

    def test_schema_unknown_type_preserves_unknown_fields(self):
        class X:
            pass

        sch = _schema_from_type(X)
        self.assertEqual("object", sch.type)
        self.assertTrue(sch.x_kubernetes_preserve_unknown_fields)

    def test_schema_from_dataclass_type_errors(self):
        with self.assertRaises(TypeError):
            _schema_from_dataclass_type(int)  # not a dataclass

    def test_resource_schema_requires_dataclass(self):
        with self.assertRaises(TypeError):
            _resource_schema(int)

    def test_default_column_type_fallback(self):
        self.assertEqual("string", _default_column_type(bytes))

    def test_resource_schema_does_not_require_spec_or_status(self):
        @dataclass(kw_only=True)
        class Spec:
            replicas: int

        @dataclass(kw_only=True, frozen=True, slots=True)
        class Res(K8sResource):
            spec: Spec
            status: dict[str, Any] | None = None

        sch = _resource_schema(Res)
        self.assertIn("spec", sch.properties)
        self.assertIn("status", sch.properties)
        self.assertTrue(sch.required is None or sch.required == [])

    def test_resource_schema_iterates_all_dataclass_fields(self):
        @dataclass(kw_only=True, frozen=True, slots=True)
        class Res(K8sResource):
            anything: int | None = None

        sch = _resource_schema(Res)
        self.assertNotIn("metadata", sch.properties)
        self.assertIn("anything", sch.properties)

    def test_collect_field_with_crd_spec_but_no_printer_column_still_recurses(self):
        @dataclass(kw_only=True)
        class Inner:
            ready: bool = crd_field(spec=CRDFieldSpec(printer_column=PrinterColumn(name="Ready")))

        @dataclass(kw_only=True)
        class Spec:
            # crd_field used, but no printer_column
            inner: Inner = crd_field(spec=CRDFieldSpec(description="just schema metadata"))

        @dataclass(kw_only=True)
        class Res:
            spec: Spec

        cols = _collect_printer_columns(Res)

        # We should NOT get a column for "inner" itself, but we should still get the nested "ready" column.
        self.assertEqual(1, len(cols))
        self.assertEqual("Ready", cols[0].name)
        self.assertEqual(".spec.inner.ready", cols[0].jsonPath)


class TestBuildCRD(unittest.TestCase):
    def _make_versions(self):
        @dataclass(kw_only=True)
        class Spec:
            replicas: int

        @dataclass(kw_only=True)
        class Status:
            replicas: int = 0

        @dataclass(slots=True, kw_only=True, frozen=True)
        class V1(CustomK8sResource):
            apiVersion = "example.com/v1"
            kind = "Widget"
            group_ = "example.com"
            plural_ = "widgets"
            is_namespaced_ = True

            served_ = True
            storage_ = False
            deprecated_ = None
            deprecation_warning_ = None
            subresources_ = None

            spec: Spec
            status: Status | None = None

        @dataclass(slots=True, kw_only=True, frozen=True)
        class V2(CustomK8sResource):
            apiVersion = "example.com/v2"
            kind = "Widget"
            group_ = "example.com"
            plural_ = "widgets"
            is_namespaced_ = True

            served_ = True
            storage_ = False
            deprecated_ = True
            deprecation_warning_ = "deprecated"
            subresources_ = None

            spec: Spec
            status: Status | None = None

        return V1, V2

    def test_storage_defaults_to_last_version(self):
        V1, V2 = self._make_versions()

        @dataclass
        class Def(CustomK8sResourceDefinition):
            versions = [V1, V2]

        crd = Def().build()
        self.assertFalse(crd.spec.versions[0].storage)
        self.assertTrue(crd.spec.versions[1].storage)

    def test_group_kind_plural_mismatch_branches(self):
        V1, V2 = self._make_versions()

        @dataclass
        class Def(CustomK8sResourceDefinition):
            versions = [V1, V2]

        V2.group_ = "other.com"
        with self.assertRaises(ValueError):
            Def().build()
        V2.group_ = "example.com"

        V2.kind = "Other"
        with self.assertRaises(ValueError):
            Def().build()
        V2.kind = "Widget"

        V2.plural_ = "others"
        with self.assertRaises(ValueError):
            Def().build()


    def test_non_resource_version_class_raises_typeerror(self):
        V1, _V2 = self._make_versions()

        @dataclass
        class Def(CustomK8sResourceDefinition):
            versions = [V1, int]  # not a CustomK8sResource

        with self.assertRaises(TypeError):
            Def().build()


class TestRecursePrinterColumns(unittest.TestCase):
    def _mk_item(self):
        @dataclass(kw_only=True)
        class Item:
            name: str = crd_field(spec=CRDFieldSpec(printer_column=PrinterColumn(name="Name")))

        return Item

    def test_direct_nested_dataclass(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(Item, ["spec", "item"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.item.name", cols[0].jsonPath)

    def test_optional_nested_dataclass(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(Optional[Item], ["spec", "maybe"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.maybe.name", cols[0].jsonPath)

    def test_annotated_optional_nested_dataclass(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(Annotated[Optional[Item], "x"], ["spec", "ann"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.ann.name", cols[0].jsonPath)

    def test_list_of_dataclass_adds_star(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(list[Item], ["spec", "items"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.items[*].name", cols[0].jsonPath)

    def test_list_of_optional_dataclass_adds_star(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(list[Optional[Item]], ["spec", "items"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.items[*].name", cols[0].jsonPath)

    def test_list_of_annotated_dataclass_adds_star(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(list[Annotated[Item, "x"]], ["spec", "items"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.items[*].name", cols[0].jsonPath)

    def test_tuple_ellipsis_of_dataclass_adds_star(self):
        Item = self._mk_item()
        cols = _recurse_printer_columns(tuple[Item, ...], ["spec", "tups"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.tups[*].name", cols[0].jsonPath)

    def test_tuple_fixed_len_uses_first_arg_adds_star(self):
        # Covers the "origin is tuple but not (...)" branch -> item_type = args[0]
        Item = self._mk_item()
        cols = _recurse_printer_columns(tuple[Item, int], ["spec", "tups"])
        self.assertEqual(1, len(cols))
        self.assertEqual(".spec.tups[*].name", cols[0].jsonPath)

    def test_list_without_args_returns_empty(self):
        # list with no args -> args empty -> item_type Any -> not dataclass -> []
        cols = _recurse_printer_columns(list, ["spec", "items"])
        self.assertEqual([], cols)

    def test_list_of_scalars_returns_empty(self):
        cols = _recurse_printer_columns(list[int], ["spec", "vals"])
        self.assertEqual([], cols)

    def test_scalar_type_returns_empty(self):
        cols = _recurse_printer_columns(int, ["spec", "n"])
        self.assertEqual([], cols)

    def test_optional_scalar_returns_empty(self):
        cols = _recurse_printer_columns(Optional[int], ["spec", "n"])
        self.assertEqual([], cols)

    def test_non_optional_union_returns_empty(self):
        # Should NOT be treated as optional -> falls through to return []
        cols = _recurse_printer_columns(Union[int, str], ["spec", "u"])
        self.assertEqual([], cols)
