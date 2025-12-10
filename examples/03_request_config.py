#!/usr/bin/env python3
"""
kubesdk Request Configuration Example

Demonstrates client-specific features for controlling request behavior:
- Custom timeouts for slow clusters or large responses
- Retry logic with backoff for transient failures
- Request/response logging for debugging
- Handling specific error statuses

These configurations can be passed to any CRUD operation.
"""

import asyncio

from kubesdk.login import login
from kubesdk import (
    get_k8s_resource,
    create_k8s_resource,
    delete_k8s_resource,
    APIRequestProcessingConfig,
    K8sAPIRequestLoggingConfig,
    NotFoundError,
    ServiceUnavailableError,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def custom_timeouts():
    """
    Configure custom timeouts for slow operations.

    Useful for:
    - Slow clusters or high-latency networks
    - Large list operations
    - Operations that take longer than the default 30s
    """
    processing = APIRequestProcessingConfig(
        http_timeout=60,  # 60 seconds instead of default 30
    )

    configmaps = await get_k8s_resource(
        ConfigMap,
        namespace=NAMESPACE,
        processing=processing,
    )

    print(f"Listed {len(configmaps.items)} ConfigMaps")


async def retry_configuration():
    """
    Configure retry behavior for transient failures.

    The client will automatically retry on specified statuses
    with configurable backoff.
    """
    # Production example - fixed interval retries
    processing_fixed = APIRequestProcessingConfig(
        backoff_limit=5,        # retry up to 5 times
        backoff_interval=2,     # wait 2 seconds between retries
        retry_statuses=[503, 504, ServiceUnavailableError],  # retry on these
    )

    # Production example - exponential backoff using a callable
    processing_exponential = APIRequestProcessingConfig(
        backoff_limit=4,
        backoff_interval=lambda attempt: 2 ** attempt,  # 2, 4, 8, 16 seconds
        retry_statuses=[503, 504],
    )

    # Use with operations - retries trigger on matching error statuses
    configmap = await get_k8s_resource(
        ConfigMap,
        name="kube-root-ca.crt",
        namespace=NAMESPACE,
        processing=processing_fixed,
    )

    print(f"Fetched: {configmap.metadata.name}")


async def verbose_logging():
    """
    Enable detailed logging for debugging.

    Logs request/response details to help diagnose issues.
    """
    # Log everything - useful for debugging
    logging_verbose = K8sAPIRequestLoggingConfig(
        on_success=True,       # log successful requests (not just errors)
        request_body=True,     # include request payload in logs
        response_body=True,    # include response body in logs
    )

    cm = ConfigMap(
        metadata=ObjectMeta(name="logging-test", namespace=NAMESPACE),
        data={"key": "value"},
    )

    # This will log the full request/response
    created = await create_k8s_resource(cm, log=logging_verbose)
    print(f"Created with verbose logging: {created.metadata.name}")

    await delete_k8s_resource(created, log=logging_verbose)
    print("Deleted with verbose logging")


async def suppress_expected_errors():
    """
    Suppress logging for expected error statuses.

    Useful when 404 or other errors are expected and shouldn't
    clutter logs.
    """
    logging_config = K8sAPIRequestLoggingConfig(
        not_error_statuses=[404, NotFoundError],  # don't log 404 as error
    )

    result = await get_k8s_resource(
        ConfigMap,
        name="non-existent-configmap",
        namespace=NAMESPACE,
        log=logging_config,
        return_api_exceptions=[404],
    )

    if isinstance(result, Exception):
        print("ConfigMap not found (404 not logged as error)")


async def combined_configuration():
    """
    Combine processing and logging configs for production use.
    """
    # Production-ready configuration
    processing = APIRequestProcessingConfig(
        http_timeout=45,
        backoff_limit=3,
        backoff_interval=lambda attempt: min(2 ** attempt, 30),  # cap at 30s
        retry_statuses=[502, 503, 504],
    )

    logging = K8sAPIRequestLoggingConfig(
        on_success=False,           # quiet on success
        request_body=False,         # don't log sensitive data
        response_body=lambda r: r.get("kind") == "Status",  # log only Status responses
        errors_as_critical=False,
    )

    configmap = await get_k8s_resource(
        ConfigMap,
        name="kube-root-ca.crt",
        namespace=NAMESPACE,
        processing=processing,
        log=logging,
    )

    print(f"Fetched: {configmap.metadata.name}")


async def main():
    await login()

    await custom_timeouts()
    await retry_configuration()
    await verbose_logging()
    await suppress_expected_errors()
    await combined_configuration()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
