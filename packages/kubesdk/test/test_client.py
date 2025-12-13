import unittest
from enum import Enum

# Use package-level import to not miss anything in __init__
from kubesdk import QueryLabelSelector, QueryLabelSelectorRequirement, LabelSelectorOp, \
    FieldSelectorRequirement, FieldSelectorOp, FieldSelector, K8sQueryParams, DryRun, PropagationPolicy


class TestQueryLabelSelector(unittest.TestCase):
    def test_only_match_labels(self):
        sel = QueryLabelSelector(matchLabels={"app": "nginx", "tier": "frontend"})
        self.assertEqual(sel.to_query_value(), "app=nginx,tier=frontend")

    def test_only_match_expressions_in_notin(self):
        expr1 = QueryLabelSelectorRequirement(
            key="env",
            op=LabelSelectorOp.In,
            values=("prod", "staging"),
        )
        expr2 = QueryLabelSelectorRequirement(
            key="tier",
            op=LabelSelectorOp.NotIn,
            values=("cache",),
        )
        sel = QueryLabelSelector(matchExpressions=(expr1, expr2))
        self.assertEqual(
            sel.to_query_value(),
            "env in (prod,staging),tier notin (cache)",
        )

    def test_exists_and_does_not_exist_with_labels(self):
        expr1 = QueryLabelSelectorRequirement(
            key="env",
            op=LabelSelectorOp.Exists,
        )
        expr2 = QueryLabelSelectorRequirement(
            key="debug",
            op=LabelSelectorOp.DoesNotExist,
        )
        sel = QueryLabelSelector(
            matchLabels={"app": "nginx"},
            matchExpressions=(expr1, expr2),
        )
        self.assertEqual(sel.to_query_value(), "app=nginx,env,!debug")

    def test_label_selector_empty(self):
        sel = QueryLabelSelector()
        self.assertEqual(sel.to_query_value(), "")

    def test_invalid_op(self):
        class FakeOp(str, Enum):
            Fake = "Fake"
        expr = QueryLabelSelectorRequirement(
            key="env",
            op=FakeOp.Fake,  # type: ignore[arg-type]
            values=("prod",),
        )
        sel = QueryLabelSelector(matchExpressions=(expr,))
        with self.assertRaises(ValueError):
            sel.to_query_value()

class TestFieldSelector(unittest.TestCase):
    def test_single_eq(self):
        req = FieldSelectorRequirement(
            field="metadata.name",
            op=FieldSelectorOp.eq,
            value="nginx",
        )
        sel = FieldSelector(requirements=(req,))
        self.assertEqual(sel.to_query_value(), "metadata.name=nginx")

    def test_single_neq(self):
        req = FieldSelectorRequirement(
            field="status.phase",
            op=FieldSelectorOp.neq,
            value="Running",
        )
        sel = FieldSelector(requirements=(req,))
        self.assertEqual(sel.to_query_value(), "status.phase!=Running")

    def test_multiple_requirements(self):
        req1 = FieldSelectorRequirement(
            field="metadata.namespace",
            op=FieldSelectorOp.eq,
            value="default",
        )
        req2 = FieldSelectorRequirement(
            field="spec.nodeName",
            op=FieldSelectorOp.neq,
            value="node1",
        )
        sel = FieldSelector(requirements=(req1, req2))
        self.assertEqual(
            sel.to_query_value(),
            "metadata.namespace=default,spec.nodeName!=node1",
        )


class TestK8sQueryParams(unittest.TestCase):
    def test_empty(self):
        params = K8sQueryParams()
        self.assertEqual(params.to_http_params(), [])

    def test_basic_scalars_bools_enums(self):
        params = K8sQueryParams(
            pretty="true",
            _continue="token123",
            limit=10,
            resourceVersion="rv1",
            timeoutSeconds=5,
            watch=True,
            allowWatchBookmarks=False,
            gracePeriodSeconds=30,
            propagationPolicy=PropagationPolicy.Foreground,
            dryRun=DryRun.All,
            fieldManager="manager",
            force=True,
        ).to_http_params()
        self.assertEqual(
            params,
            [
                ("pretty", "true"),
                ("continue", "token123"),
                ("limit", "10"),
                ("resourceVersion", "rv1"),
                ("timeoutSeconds", "5"),
                ("watch", "true"),
                ("allowWatchBookmarks", "false"),
                ("gracePeriodSeconds", "30"),
                ("propagationPolicy", "Foreground"),
                ("dryRun", "All"),
                ("fieldManager", "manager"),
                ("force", "true"),
            ],
        )

    def test_field_and_label_selector_objects(self):
        field_sel = FieldSelector(
            requirements=(
                FieldSelectorRequirement(
                    field="metadata.name",
                    op=FieldSelectorOp.eq,
                    value="nginx",
                ),
            ),
        )
        label_sel = QueryLabelSelector(matchLabels={"app": "nginx"})
        params = K8sQueryParams(
            fieldSelector=field_sel,
            labelSelector=label_sel,
        ).to_http_params()
        self.assertEqual(
            params,
            [
                ("fieldSelector", "metadata.name=nginx"),
                ("labelSelector", "app=nginx")
            ],
        )

    def test_field_and_label_selector_strings(self):
        params = K8sQueryParams(
            fieldSelector=FieldSelector(requirements=[
                FieldSelectorRequirement(field="metadata.namespace", op=FieldSelectorOp.eq, value="default")]),
            labelSelector=QueryLabelSelector(matchLabels={"app": "nginx"})
        ).to_http_params()
        self.assertEqual(
            params,
            [
                ("fieldSelector", "metadata.namespace=default"),
                ("labelSelector", "app=nginx")
            ],
        )
