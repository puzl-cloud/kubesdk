#!/usr/bin/env python3
"""
kubesdk Watch Example

Demonstrates real-time resource watching:
- Watch streams events as resources change
- Filter watches with label selectors
- Event types: ADDED, MODIFIED, DELETED, BOOKMARK

Prerequisites:
- A valid kubeconfig at ~/.kube/config or running inside a Kubernetes pod
- Access to watch/create/delete ConfigMaps in the 'default' namespace
"""

import asyncio
from contextlib import suppress

from kubesdk import (
    login,
    create_k8s_resource,
    delete_k8s_resource,
    NotFoundError,
    watch_k8s_resources,
    WatchEventType,
    K8sQueryParams,
    QueryLabelSelector,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def watch_configmaps(max_events: int = 5) -> None:
    """
    Watch ConfigMaps and print events.

    When a watch starts, it first receives ADDED events for all existing
    resources, then streams new events as changes occur.
    """
    print(f"Watching ConfigMaps (max {max_events} events)...")

    count = 0
    async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE):
        if event.type == WatchEventType.BOOKMARK:
            continue  # Bookmarks are for resumption, skip

        count += 1
        print(f"  {event.type}: {event.object.metadata.name}")

        if count >= max_events:
            break


async def watch_with_filter(label: str, value: str, max_events: int = 3) -> None:
    """Watch only ConfigMaps matching a label selector (server-side filtering)."""
    print(f"Watching ConfigMaps with label '{label}={value}'...")

    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels={label: value})
    )

    count = 0
    async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE, params=params):
        if event.type == WatchEventType.BOOKMARK:
            continue

        count += 1
        print(f"  {event.type}: {event.object.metadata.name}")

        if count >= max_events:
            break


async def watch_for_prefix(prefix: str, max_events: int = 6) -> None:
    """Watch for ConfigMaps with names starting with prefix."""
    count = 0
    async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE):
        if event.type == WatchEventType.BOOKMARK:
            continue

        name = event.object.metadata.name
        if name.startswith(prefix):
            count += 1
            print(f"  Event: {event.type} {name}")
            if count >= max_events:
                break


async def create_and_delete_configmaps(prefix: str, count: int = 3) -> None:
    """Create and delete ConfigMaps to generate watch events."""
    for i in range(count):
        cm = ConfigMap(
            metadata=ObjectMeta(name=f"{prefix}-{i}", namespace=NAMESPACE),
            data={"index": str(i)},
        )
        await create_k8s_resource(cm)
        print(f"  Created: {prefix}-{i}")
        await asyncio.sleep(0.3)

    await asyncio.sleep(0.5)

    for i in range(count):
        with suppress(NotFoundError):
            await delete_k8s_resource(ConfigMap, name=f"{prefix}-{i}", namespace=NAMESPACE)
            print(f"  Deleted: {prefix}-{i}")


async def main():
    await login()

    # 1. Basic watch - shows existing ConfigMaps as ADDED events
    print("\n=== Basic Watch ===")
    try:
        await asyncio.wait_for(watch_configmaps(max_events=3), timeout=3)
    except TimeoutError:
        print("  (timeout - no more events)")

    # 2. Watch with live events - create/delete while watching
    print("\n=== Watch with Live Events ===")
    await asyncio.gather(
        asyncio.wait_for(watch_for_prefix("watch-test", max_events=6), timeout=10),
        create_and_delete_configmaps("watch-test", count=3),
    )

    # 3. Filtered watch - only matching labels
    print("\n=== Filtered Watch ===")
    for i in range(2):
        cm = ConfigMap(
            metadata=ObjectMeta(name=f"filtered-{i}", namespace=NAMESPACE, labels={"team": "backend"}),
            data={"i": str(i)},
        )
        await create_k8s_resource(cm)

    try:
        await asyncio.wait_for(watch_with_filter("team", "backend", max_events=2), timeout=3)
    except TimeoutError:
        print("  (timeout)")

    for i in range(2):
        with suppress(NotFoundError):
            await delete_k8s_resource(ConfigMap, name=f"filtered-{i}", namespace=NAMESPACE)

    print("\nDone")


if __name__ == "__main__":
    asyncio.run(main())
