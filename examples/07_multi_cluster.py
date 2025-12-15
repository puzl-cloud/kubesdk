#!/usr/bin/env python3
"""
kubesdk Multi-Cluster Operations Example

Demonstrates working with multiple Kubernetes clusters:
- Connecting to different clusters via kubeconfig contexts
- Using server= parameter to target specific clusters
- Parallel operations across clusters

kubesdk manages separate connection pools for each cluster.
"""

import asyncio

from kubesdk import (
    login,
    KubeConfig,
    create_k8s_resource,
    get_k8s_resource,
    delete_k8s_resource,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap, Namespace
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def connect_to_clusters() -> dict[str, str]:
    """
    Connect to multiple clusters using different kubeconfig contexts.

    Returns dict of cluster_name -> server_url
    """
    clusters = {}

    # Connect to first cluster (default context)
    server_info = await login()
    clusters["primary"] = server_info.server
    print(f"Connected to primary: {server_info.server}")

    # Connect to second cluster using specific context
    # Change context_name to match your kubeconfig
    server_info = await login(KubeConfig(context_name="minikube"))
    clusters["secondary"] = server_info.server
    print(f"Connected to secondary: {server_info.server}")

    return clusters


async def operations_on_specific_cluster(clusters: dict[str, str]):
    """
    Execute operations on a specific cluster using server= parameter.
    """
    primary = clusters["primary"]

    # Create ConfigMap on primary cluster
    cm = ConfigMap(
        metadata=ObjectMeta(
            name="multi-cluster-test",
            namespace=NAMESPACE,
            labels={"example": "multi-cluster"},
        ),
        data={"cluster": "primary"},
    )

    created = await create_k8s_resource(cm, server=primary)
    print(f"Created on primary: {created.metadata.name}")

    # Read from primary cluster
    fetched = await get_k8s_resource(
        ConfigMap,
        name="multi-cluster-test",
        namespace=NAMESPACE,
        server=primary,
    )
    print(f"Fetched from primary: data={fetched.data}")

    # Cleanup
    await delete_k8s_resource(
        ConfigMap,
        name="multi-cluster-test",
        namespace=NAMESPACE,
        server=primary,
    )
    print("Deleted from primary")


async def parallel_cluster_query(clusters: dict[str, str]):
    """
    Query multiple clusters in parallel.
    """
    async def count_namespaces(name: str, server: str) -> tuple[str, int]:
        namespaces = await get_k8s_resource(Namespace, server=server)
        return name, len(namespaces.items)

    # Query all clusters in parallel
    results = await asyncio.gather(*[
        count_namespaces(name, server)
        for name, server in clusters.items()
    ])

    print("Namespace counts:")
    for name, count in results:
        print(f"  {name}: {count}")


async def main():
    # Connect to clusters
    clusters = await connect_to_clusters()

    # Operations on specific cluster
    await operations_on_specific_cluster(clusters)

    # Parallel queries
    await parallel_cluster_query(clusters)

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
