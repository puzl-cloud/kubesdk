#!/usr/bin/env python3
"""
kubesdk Error Handling Example

Demonstrates error handling patterns:
- Typed exceptions for HTTP status codes
- return_api_exceptions for non-throwing errors
- Kubernetes Status objects for error details
"""

import asyncio

from kubesdk.login import login, KubeConfig
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    delete_k8s_resource,
    RESTAPIError,
    NotFoundError,
    ConflictError,
    BadRequestError,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta, Status

NAMESPACE = "default"


async def catch_not_found():
    """Handle 404 NotFoundError with Status details."""
    try:
        await get_k8s_resource(
            ConfigMap,
            name="does-not-exist",
            namespace=NAMESPACE,
        )
    except NotFoundError as e:
        print(f"NotFoundError: status={e.status}")
        if e.extra and isinstance(e.extra, Status):
            print(f"  reason={e.extra.reason}, message={e.extra.message}")


async def catch_conflict():
    """Handle 409 ConflictError when creating duplicate resource."""
    cm = ConfigMap(
        metadata=ObjectMeta(name="conflict-test", namespace=NAMESPACE),
        data={"key": "value"},
    )

    try:
        await create_k8s_resource(cm)
        await create_k8s_resource(cm)  # Conflict
    except ConflictError as e:
        print(f"ConflictError: status={e.status}, reason={e.extra.reason if e.extra else 'N/A'}")
    finally:
        await delete_k8s_resource(
            ConfigMap, name="conflict-test", namespace=NAMESPACE,
            return_api_exceptions=[404]
        )


async def catch_validation_error():
    """Handle 422/400 validation errors."""
    invalid_cm = ConfigMap(
        metadata=ObjectMeta(name="invalid/name", namespace=NAMESPACE),  # Invalid name
        data={"key": "value"},
    )

    try:
        await create_k8s_resource(invalid_cm)
    except (BadRequestError, RESTAPIError) as e:
        print(f"ValidationError: status={e.status}")
        if e.extra and hasattr(e.extra, 'message'):
            print(f"  message={e.extra.message}")


async def return_error_instead_of_raise():
    """
    Use return_api_exceptions to get errors as return values.

    Useful for batch operations or cleaner control flow.
    """
    result = await get_k8s_resource(
        ConfigMap,
        name="might-not-exist",
        namespace=NAMESPACE,
        return_api_exceptions=[404],
    )

    if isinstance(result, RESTAPIError):
        print(f"Returned error: {type(result).__name__}, status={result.status}")
    else:
        print(f"Found: {result.metadata.name}")


async def batch_with_error_aggregation():
    """Aggregate errors from batch operations."""
    names = ["batch-1", "batch-2", "batch-3"]
    results = {"found": [], "not_found": []}

    for name in names:
        result = await get_k8s_resource(
            ConfigMap,
            name=name,
            namespace=NAMESPACE,
            return_api_exceptions=[404],
        )

        if isinstance(result, NotFoundError):
            results["not_found"].append(name)
        else:
            results["found"].append(name)

    print(f"Batch results: found={len(results['found'])}, not_found={len(results['not_found'])}")


async def handle_login_error():
    """Handle login errors for invalid context."""
    try:
        await login(KubeConfig(context_name="non-existent-context"))
    except Exception as e:
        print(f"Login error: {type(e).__name__}")


async def main():
    await login()

    await catch_not_found()
    await catch_conflict()
    await catch_validation_error()
    await return_error_instead_of_raise()
    await batch_with_error_aggregation()
    await handle_login_error()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
