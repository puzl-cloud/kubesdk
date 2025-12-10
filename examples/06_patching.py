#!/usr/bin/env python3
"""
kubesdk Patching and Update Strategies Example

This example demonstrates the sophisticated update/patch system in kubesdk:
- Strategic Merge Patch (Kubernetes-native, smart array handling)
- JSON Patch (RFC 6902, precise operations)
- Full replacement (PUT)
- Path-based selective updates
- Conflict detection and handling

kubesdk automatically selects the best patch strategy based on:
1. Resource type support
2. Whether you provide `built_from_latest`
3. Whether you specify `paths`
4. Whether you use `force=True`
"""

import asyncio
from dataclasses import replace

from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
    NotFoundError,
    ConflictError,
)
from kubesdk.path_picker import from_root_, path_

from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def setup_test_configmap() -> ConfigMap:
    """Create a ConfigMap for testing patch operations."""
    cm = ConfigMap(
        metadata=ObjectMeta(
            name="patch-example",
            namespace=NAMESPACE,
            labels={"app": "patch-demo", "version": "v1"},
            annotations={"description": "Original annotation"},
        ),
        data={
            "config.json": '{"setting1": "value1", "setting2": "value2"}',
            "app.properties": "key1=value1\nkey2=value2",
        },
    )

    # Cleanup if exists (suppress 404 logs)
    await delete_k8s_resource(
        ConfigMap, name="patch-example", namespace=NAMESPACE,
        return_api_exceptions=[404]
    )

    return await create_k8s_resource(cm)


async def strategic_merge_patch_example():
    """
    Strategic Merge Patch (default for most resources).

    This is Kubernetes' native patch format that:
    - Merges objects recursively
    - Handles arrays intelligently based on merge keys
    - Allows null values to delete fields
    """
    original = await get_k8s_resource(
        ConfigMap,
        name="patch-example",
        namespace=NAMESPACE,
    )

    # Modify the resource - replace nested frozen dataclasses from innermost to outermost
    new_labels = dict(original.metadata.labels)
    new_labels["environment"] = "staging"  # Add new label
    new_labels["version"] = "v2"  # Update existing label

    new_data = dict(original.data)
    new_data["new-key"] = "new-value"  # Add new data key
    new_data["config.json"] = '{"setting1": "updated"}'  # Update existing

    new_metadata = replace(original.metadata, labels=new_labels)
    modified = replace(original, metadata=new_metadata, data=new_data)

    # Update with diff-based strategic merge patch
    updated = await update_k8s_resource(
        modified,
        built_from_latest=original,  # Enables diff computation
    )

    print(f"Strategic merge patch: labels={list(updated.metadata.labels.keys())}")

    return updated


async def selective_field_update():
    """
    Update only specific fields using PathPicker.

    This is powerful for:
    - Updating only what you changed
    - Avoiding conflicts on unrelated fields
    - Precise control over what gets patched
    """
    original = await get_k8s_resource(
        ConfigMap,
        name="patch-example",
        namespace=NAMESPACE,
    )

    # Modify multiple fields - replace nested frozen dataclasses
    new_labels = dict(original.metadata.labels)
    new_labels["selective"] = "update"

    new_annotations = dict(original.metadata.annotations or {})
    new_annotations["new-annotation"] = "value"

    new_data = dict(original.data)
    new_data["selective-key"] = "selective-value"

    new_metadata = replace(original.metadata, labels=new_labels, annotations=new_annotations)
    modified = replace(original, metadata=new_metadata, data=new_data)

    # But only update the labels - ignore other changes!
    cm = from_root_(ConfigMap)
    updated = await update_k8s_resource(
        modified,
        built_from_latest=original,
        paths=[
            path_(cm.metadata.labels),  # Only update labels
        ],
    )

    print(f"Selective update: only labels changed, data keys={list(updated.data.keys())}")

    return updated


async def update_nested_fields():
    """
    Update deeply nested fields with type-safe paths.

    PathPicker provides IDE autocomplete and type checking.
    """
    # Create a ConfigMap with nested JSON
    cm = ConfigMap(
        metadata=ObjectMeta(
            name="nested-example",
            namespace=NAMESPACE,
            labels={"level1": "value1"},
        ),
        data={
            "database": "host=localhost",
            "cache": "redis://localhost",
        },
    )

    await delete_k8s_resource(
        ConfigMap, name="nested-example", namespace=NAMESPACE,
        return_api_exceptions=[404]
    )

    original = await create_k8s_resource(cm)

    # Update specific nested fields
    new_data = dict(original.data)
    new_data["database"] = "host=production.db"
    new_data["new-config"] = "value"  # This won't be patched

    modified = replace(original, data=new_data)

    # Build typed path expressions
    cm_type = from_root_(ConfigMap)

    updated = await update_k8s_resource(
        modified,
        built_from_latest=original,
        paths=[
            path_(cm_type.data["database"]),  # Only update this specific key
        ],
    )

    print(f"Nested field update: data={updated.data}")

    await delete_k8s_resource(ConfigMap, name="nested-example", namespace=NAMESPACE)
    return updated


async def force_full_replacement():
    """
    Force full resource replacement (PUT instead of PATCH).

    Use when you want to:
    - Replace the entire resource spec
    - Ensure no leftover fields from previous versions
    - Avoid merge semantics entirely
    """
    original = await get_k8s_resource(
        ConfigMap,
        name="patch-example",
        namespace=NAMESPACE,
    )

    # Create completely new spec
    replacement = ConfigMap(
        metadata=ObjectMeta(
            name="patch-example",
            namespace=NAMESPACE,
            labels={"app": "replaced"},
            resourceVersion=original.metadata.resourceVersion,  # Required for PUT
        ),
        data={
            "completely": "new",
            "config": "data",
        },
    )

    # Force PUT instead of PATCH
    updated = await update_k8s_resource(
        replacement,
        force=True,  # Use PUT, not PATCH
    )

    print(f"Full replacement (PUT): data={list(updated.data.keys())}")

    return updated


async def json_patch_with_conflict_guards():
    """
    JSON Patch (RFC 6902) with conflict detection.

    kubesdk automatically adds 'test' operations to guard against
    concurrent modifications to list items. This prevents race conditions.

    JSON Patch operations:
    - add: Add a value
    - remove: Remove a value
    - replace: Replace a value
    - move: Move a value
    - copy: Copy a value
    - test: Test that a value equals expected (guard)
    """
    # Recreate for clean state
    original = await setup_test_configmap()

    # Simulate concurrent modification scenario
    new_data = dict(original.data)
    new_data["config.json"] = '{"concurrent": "update"}'
    modified = replace(original, data=new_data)

    # When using built_from_latest, kubesdk:
    # 1. Computes diff as JSON Patch operations
    # 2. For resources that don't support strategic merge, uses JSON Patch
    # 3. Adds test guards for list operations to detect conflicts

    updated = await update_k8s_resource(
        modified,
        built_from_latest=original,
        ignore_list_conflicts=False,  # Enable conflict guards (default)
    )

    print(f"JSON patch with guards: updated config.json")

    return updated


async def handle_update_conflicts():
    """
    Demonstrate handling of update conflicts (409 Conflict).

    Conflicts occur when:
    - resourceVersion doesn't match (optimistic locking)
    - JSON Patch test operation fails
    - Another process modified the resource concurrently
    """
    original = await get_k8s_resource(
        ConfigMap,
        name="patch-example",
        namespace=NAMESPACE,
    )

    # Simulate a stale version by modifying resourceVersion
    # Replace nested frozen dataclasses from innermost to outermost
    stale_metadata = replace(original.metadata, resourceVersion="1")
    stale_version = replace(original, metadata=stale_metadata)

    try:
        await update_k8s_resource(
            stale_version,
            force=True,  # PUT requires matching resourceVersion
        )
    except ConflictError:
        # Retry pattern: fetch latest and retry
        fresh = await get_k8s_resource(
            ConfigMap,
            name="patch-example",
            namespace=NAMESPACE,
        )
        new_data = dict(fresh.data)
        new_data["retried"] = "success"
        modified_fresh = replace(fresh, data=new_data)

        await update_k8s_resource(
            modified_fresh,
            built_from_latest=fresh,
        )
        print("Conflict handling: caught 409, retried successfully")


async def main():
    """Run all patching examples."""
    await login()

    try:
        await setup_test_configmap()
        await strategic_merge_patch_example()
        await selective_field_update()
        await update_nested_fields()
        await force_full_replacement()
        await json_patch_with_conflict_guards()
        await handle_update_conflicts()

    finally:
        await delete_k8s_resource(
            ConfigMap, name="patch-example", namespace=NAMESPACE,
            return_api_exceptions=[404]
        )

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
