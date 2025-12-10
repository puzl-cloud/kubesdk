#!/usr/bin/env python3
"""
kubesdk Batch Operations Example

Demonstrates efficient bulk operations:
- Parallel resource creation with asyncio.gather
- Batch updates using label selectors
- Bulk deletion with error handling
"""

import asyncio
import time

from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
)
from kubesdk.client import K8sQueryParams, QueryLabelSelector

from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"
BATCH_LABEL = {"batch-example": "true"}


async def parallel_create(count: int = 10) -> int:
    """Create multiple ConfigMaps in parallel using asyncio.gather."""
    resources = [
        ConfigMap(
            metadata=ObjectMeta(
                name=f"batch-cm-{i}",
                namespace=NAMESPACE,
                labels=BATCH_LABEL,
            ),
            data={"index": str(i)},
        )
        for i in range(count)
    ]

    start = time.perf_counter()
    results = await asyncio.gather(
        *[create_k8s_resource(r) for r in resources],
        return_exceptions=True,
    )
    duration = time.perf_counter() - start

    succeeded = sum(1 for r in results if not isinstance(r, Exception))
    print(f"Created {succeeded}/{count} in {duration:.2f}s ({count/duration:.1f} ops/sec)")

    return succeeded


async def batch_update_by_label() -> int:
    """Update all ConfigMaps matching a label selector."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels=BATCH_LABEL)
    )

    configmaps = await get_k8s_resource(ConfigMap, namespace=NAMESPACE, params=params)
    print(f"Found {len(configmaps.items)} ConfigMaps to update")

    if not configmaps.items:
        return 0

    async def update_one(cm: ConfigMap) -> ConfigMap:
        cm.data["updated"] = "true"
        return await update_k8s_resource(cm, built_from_latest=cm)

    start = time.perf_counter()
    results = await asyncio.gather(
        *[update_one(cm) for cm in configmaps.items],
        return_exceptions=True,
    )
    duration = time.perf_counter() - start

    succeeded = sum(1 for r in results if not isinstance(r, Exception))
    print(f"Updated {succeeded}/{len(configmaps.items)} in {duration:.2f}s")

    return succeeded


async def batch_delete() -> int:
    """Delete all ConfigMaps matching label selector."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels=BATCH_LABEL)
    )

    configmaps = await get_k8s_resource(ConfigMap, namespace=NAMESPACE, params=params)
    print(f"Found {len(configmaps.items)} ConfigMaps to delete")

    if not configmaps.items:
        return 0

    start = time.perf_counter()
    results = await asyncio.gather(
        *[delete_k8s_resource(cm) for cm in configmaps.items],
        return_exceptions=True,
    )
    duration = time.perf_counter() - start

    deleted = sum(1 for r in results if not isinstance(r, Exception))
    print(f"Deleted {deleted}/{len(configmaps.items)} in {duration:.2f}s")

    return deleted


async def main():
    await login()

    await parallel_create(count=10)
    await batch_update_by_label()
    await batch_delete()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
