#!/usr/bin/env python3
from __future__ import annotations

"""
kubesdk Custom Resources (CRD) Example

Demonstrates working with Custom Resource Definitions using CloudNativePG.
Shows how to define K8sResource classes for CRDs and perform CRUD operations.

Prerequisites:
- Install CloudNativePG operator:
    kubectl apply --server-side -f \\
      https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/releases/cnpg-1.25.0.yaml

For production, generate typed models using kubesdk-cli:
    kubesdk --url https://your-cluster:6443 --output ./models --module-name models
"""

import asyncio
from dataclasses import dataclass, field
from typing import ClassVar, Dict  # Dict needed for lazy loading type resolution

from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
)

import kube_models
from kube_models.resource import K8sResource
from kube_models.const import PatchRequestType
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


@dataclass(slots=True, kw_only=True, frozen=True)
class Cluster(K8sResource):
    """
    CloudNativePG Cluster custom resource.

    apiVersion: postgresql.cnpg.io/v1
    kind: Cluster
    """
    apiVersion: ClassVar[str] = "postgresql.cnpg.io/v1"
    kind: ClassVar[str] = "Cluster"
    group_: ClassVar[str] = "postgresql.cnpg.io"
    plural_: ClassVar[str] = "clusters"
    api_path_: ClassVar[str] = "apis/postgresql.cnpg.io/v1/namespaces/{namespace}/clusters"
    patch_strategies_: ClassVar[set] = {PatchRequestType.merge}

    metadata: ObjectMeta = field(default_factory=ObjectMeta)
    spec: dict | None = None
    status: dict | None = None


# Register model so kubesdk can decode API responses
# Access internal registry directly (private API, may change)
vars(kube_models)["__ALL_RESOURCES"][(Cluster.apiVersion, Cluster.kind)] = Cluster


async def crud_operations():
    """Demonstrate CRUD operations on CloudNativePG Cluster."""

    # Create
    cluster = Cluster(
        metadata=ObjectMeta(name="example-pg", namespace=NAMESPACE),
        spec={
            "instances": 1,
            "storage": {"size": "1Gi"},
        },
    )
    created = await create_k8s_resource(cluster)
    print(f"Created: {created.metadata.name}")

    # Read single
    fetched = await get_k8s_resource(Cluster, name="example-pg", namespace=NAMESPACE)
    print(f"Fetched: {fetched.metadata.name}, instances={fetched.spec.get('instances')}")

    # Update
    updated_cluster = Cluster(
        metadata=fetched.metadata,
        spec={**fetched.spec, "instances": 2},
    )
    updated = await update_k8s_resource(updated_cluster, built_from_latest=fetched)
    print(f"Updated: {updated.metadata.name}, instances={updated.spec.get('instances')}")

    # Delete
    await delete_k8s_resource(Cluster, name="example-pg", namespace=NAMESPACE)
    print("Deleted: example-pg")


async def main():
    await login()
    await crud_operations()
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
