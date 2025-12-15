#!/usr/bin/env python3
"""
kubesdk Quickstart Example

Demonstrates basic CRUD operations for Kubernetes resources using kubesdk.

Prerequisites:
- A valid kubeconfig at ~/.kube/config or running inside a Kubernetes pod
- Access to create/manage ConfigMaps in the 'default' namespace
"""

import asyncio

from kubesdk import (
    login,
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
    create_or_update_k8s_resource,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta


async def main():
    # Login automatically detects credentials from:
    # - Service account token (when running in-cluster)
    # - kubeconfig file (~/.kube/config or KUBECONFIG env var)
    await login()

    namespace = "default"
    configmap_name = "kubesdk-example"

    # Create a ConfigMap
    configmap = ConfigMap(
        metadata=ObjectMeta(
            name=configmap_name,
            namespace=namespace,
            labels={"app": "kubesdk-example"},
        ),
        data={
            "database.host": "localhost",
            "database.port": "5432",
        },
    )
    created = await create_k8s_resource(configmap)
    print(f"Created: {created.metadata.name}")

    # Read the ConfigMap back
    fetched = await get_k8s_resource(ConfigMap, name=configmap_name, namespace=namespace)
    print(f"Fetched data: {fetched.data}")

    # List all ConfigMaps in namespace
    configmap_list = await get_k8s_resource(ConfigMap, namespace=namespace)
    print(f"ConfigMaps in namespace: {[cm.metadata.name for cm in configmap_list.items]}")

    # Update the ConfigMap
    fetched.data["database.host"] = "production.example.com"
    updated = await update_k8s_resource(fetched)
    print(f"Updated: {updated.metadata.name}")

    # Upsert pattern: create_or_update creates if not exists, updates if exists
    upsert_cm = ConfigMap(
        metadata=ObjectMeta(name="kubesdk-upsert-example", namespace=namespace),
        data={"key": "value"},
    )
    await create_or_update_k8s_resource(upsert_cm)

    # Second call updates the existing resource
    # Note: ConfigMap is a frozen dataclass, create a new instance to change data
    upsert_cm_v2 = ConfigMap(
        metadata=ObjectMeta(name="kubesdk-upsert-example", namespace=namespace),
        data={"key": "updated-value"},
    )
    await create_or_update_k8s_resource(upsert_cm_v2)
    print("Upsert: created then updated")

    # Delete by passing the resource instance
    await delete_k8s_resource(updated)

    # Delete by specifying type, name, and namespace
    await delete_k8s_resource(ConfigMap, name="kubesdk-upsert-example", namespace=namespace)
    print("Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
