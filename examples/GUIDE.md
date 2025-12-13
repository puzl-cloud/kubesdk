# kubesdk Complete Guide

A comprehensive guide to kubesdk - async Kubernetes client for Python.

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Authentication](#authentication)
4. [Request Configuration](#request-configuration)
5. [Working with Deployments](#working-with-deployments)
6. [Watching Resources](#watching-resources)
7. [Patching and Updates](#patching-and-updates)
8. [Multi-Cluster Operations](#multi-cluster-operations)
9. [Error Handling](#error-handling)
10. [Batch Operations](#batch-operations)
11. [Custom Resources (CRDs)](#custom-resources-crds)

---

## Installation

```bash
pip install kubesdk kube-models
```

Or with uv:
```bash
uv add kubesdk kube-models
```

**Requirements:**
- Python 3.10+
- Kubernetes cluster access via `~/.kube/config` or in-cluster service account

---

## Quick Start

Basic CRUD operations with ConfigMaps.

```python
import asyncio
from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
    create_or_update_k8s_resource,
)
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def main():
    # Connect to cluster
    await login()

    # CREATE
    cm = ConfigMap(
        metadata=ObjectMeta(name="my-config", namespace=NAMESPACE),
        data={"database_url": "postgres://localhost:5432", "debug": "false"},
    )
    created = await create_k8s_resource(cm)
    print(f"Created: {created.metadata.name}")

    # READ (single resource)
    fetched = await get_k8s_resource(ConfigMap, name="my-config", namespace=NAMESPACE)
    print(f"Fetched: {fetched.data}")

    # READ (list all in namespace)
    all_cms = await get_k8s_resource(ConfigMap, namespace=NAMESPACE)
    print(f"Found {len(all_cms.items)} ConfigMaps")

    # UPDATE
    updated_cm = ConfigMap(
        metadata=fetched.metadata,
        data={**fetched.data, "debug": "true", "new_key": "new_value"},
    )
    updated = await update_k8s_resource(updated_cm, built_from_latest=fetched)
    print(f"Updated: {updated.data}")

    # CREATE OR UPDATE (upsert)
    upsert_cm = ConfigMap(
        metadata=ObjectMeta(name="my-config", namespace=NAMESPACE),
        data={"completely": "new", "data": "here"},
    )
    upserted = await create_or_update_k8s_resource(upsert_cm)
    print(f"Upserted: {upserted.data}")

    # DELETE
    await delete_k8s_resource(ConfigMap, name="my-config", namespace=NAMESPACE)
    print("Deleted")


asyncio.run(main())
```

---

## Authentication

Multiple ways to connect to Kubernetes clusters.

```python
import asyncio
from kubesdk.login import login, KubeConfig

async def main():
    # Default login - uses current context from ~/.kube/config
    server_info = await login()
    print(f"Connected to: {server_info.server}")

    # Specific context
    await login(KubeConfig(context_name="production"))

    # Custom kubeconfig file
    await login(KubeConfig(config_file="~/.kube/custom-config"))

    # Custom kubeconfig with specific context
    await login(KubeConfig(
        config_file="~/.kube/custom-config",
        context_name="staging"
    ))


asyncio.run(main())
```

### Available KubeConfig Options

| Parameter | Description |
|-----------|-------------|
| `config_file` | Path to kubeconfig file (default: `~/.kube/config`) |
| `context_name` | Kubernetes context to use |

---

## Request Configuration

Control timeouts, retries, and logging for API requests.

### Timeouts

```python
from kubesdk import get_k8s_resource, APIRequestProcessingConfig
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap

# Default timeout is 30s, increase for slow operations
processing = APIRequestProcessingConfig(http_timeout=60)

configmaps = await get_k8s_resource(
    ConfigMap,
    namespace="default",
    processing=processing,
)
```

### Retry Configuration

```python
from kubesdk import APIRequestProcessingConfig

# Fixed interval retries
processing = APIRequestProcessingConfig(
    backoff_limit=5,           # retry up to 5 times
    backoff_interval=2,        # wait 2 seconds between retries
    retry_statuses=[503, 504], # retry on these HTTP statuses
)

# Exponential backoff
processing = APIRequestProcessingConfig(
    backoff_limit=4,
    backoff_interval=lambda attempt: 2 ** attempt,  # 2, 4, 8, 16 seconds
    retry_statuses=[503, 504],
)

# Capped exponential backoff
processing = APIRequestProcessingConfig(
    backoff_limit=5,
    backoff_interval=lambda attempt: min(2 ** attempt, 30),  # max 30 seconds
    retry_statuses=[502, 503, 504],
)
```

### Request/Response Logging

```python
from kubesdk import K8sAPIRequestLoggingConfig, create_k8s_resource

# Verbose logging for debugging
logging_config = K8sAPIRequestLoggingConfig(
    on_success=True,       # log successful requests
    request_body=True,     # include request payload
    response_body=True,    # include response body
)

await create_k8s_resource(cm, log=logging_config)

# Suppress expected errors (e.g., 404 during cleanup)
logging_config = K8sAPIRequestLoggingConfig(
    not_error_statuses=[404],  # don't log 404 as error
)

await get_k8s_resource(
    ConfigMap,
    name="maybe-missing",
    namespace="default",
    log=logging_config,
    return_api_exceptions=[404],
)
```

### Combined Production Configuration

```python
processing = APIRequestProcessingConfig(
    http_timeout=45,
    backoff_limit=3,
    backoff_interval=lambda attempt: min(2 ** attempt, 30),
    retry_statuses=[502, 503, 504],
)

logging = K8sAPIRequestLoggingConfig(
    on_success=False,
    request_body=False,
    not_error_statuses=[404],
)

await get_k8s_resource(
    ConfigMap,
    name="my-config",
    namespace="default",
    processing=processing,
    log=logging,
)
```

---

## Working with Deployments

Full lifecycle management for Kubernetes Deployments.

```python
import asyncio
from dataclasses import replace
from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
)
from kubesdk import from_root_, path_
from kube_models.api_v1.io.k8s.api.apps.v1 import Deployment, DeploymentSpec
from kube_models.api_v1.io.k8s.api.core.v1 import (
    Container, ContainerPort, PodSpec, PodTemplateSpec,
)
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta, LabelSelector

NAMESPACE = "default"


async def main():
    await login()

    # CREATE DEPLOYMENT
    deployment = Deployment(
        metadata=ObjectMeta(
            name="nginx-example",
            namespace=NAMESPACE,
            labels={"app": "nginx"},
        ),
        spec=DeploymentSpec(
            replicas=2,
            selector=LabelSelector(matchLabels={"app": "nginx"}),
            template=PodTemplateSpec(
                metadata=ObjectMeta(labels={"app": "nginx"}),
                spec=PodSpec(
                    containers=[
                        Container(
                            name="nginx",
                            image="nginx:1.24",
                            ports=[ContainerPort(containerPort=80)],
                        )
                    ]
                ),
            ),
        ),
    )

    created = await create_k8s_resource(deployment)
    print(f"Created deployment: {created.metadata.name}")

    # SCALE REPLICAS
    fetched = await get_k8s_resource(
        Deployment, name="nginx-example", namespace=NAMESPACE
    )

    # Use dataclasses.replace for frozen dataclasses
    new_spec = replace(fetched.spec, replicas=3)
    scaled = replace(fetched, spec=new_spec)

    # Use path picker to update only replicas field
    d = from_root_(Deployment)
    updated = await update_k8s_resource(
        scaled,
        built_from_latest=fetched,
        paths=[path_(d.spec.replicas)],
    )
    print(f"Scaled to {updated.spec.replicas} replicas")

    # ROLLING UPDATE (change image)
    fetched = await get_k8s_resource(
        Deployment, name="nginx-example", namespace=NAMESPACE
    )

    containers = list(fetched.spec.template.spec.containers)
    containers[0] = replace(containers[0], image="nginx:1.25")

    new_pod_spec = replace(fetched.spec.template.spec, containers=containers)
    new_template = replace(fetched.spec.template, spec=new_pod_spec)
    new_spec = replace(fetched.spec, template=new_template)
    rolling = replace(fetched, spec=new_spec)

    updated = await update_k8s_resource(rolling, built_from_latest=fetched)
    print(f"Rolling update to: {updated.spec.template.spec.containers[0].image}")

    # CHECK STATUS
    deployment = await get_k8s_resource(
        Deployment, name="nginx-example", namespace=NAMESPACE
    )
    status = deployment.status
    print(f"Status: {status.availableReplicas}/{status.replicas} available")

    # DELETE
    await delete_k8s_resource(
        Deployment, name="nginx-example", namespace=NAMESPACE
    )
    print("Deleted deployment")


asyncio.run(main())
```

---

## Watching Resources

Real-time streaming of resource changes.

```python
import asyncio
from kubesdk.login import login
from kubesdk import create_k8s_resource, delete_k8s_resource
from kubesdk.client import watch_k8s_resources, WatchEventType, K8sQueryParams, QueryLabelSelector
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def basic_watch():
    """Watch all ConfigMaps in namespace."""
    print("Watching ConfigMaps...")

    count = 0
    async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE):
        # Skip bookmark events (used for watch resumption)
        if event.type == WatchEventType.BOOKMARK:
            continue

        print(f"  {event.type}: {event.object.metadata.name}")

        count += 1
        if count >= 5:
            break


async def filtered_watch():
    """Watch only ConfigMaps with specific label."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels={"team": "backend"})
    )

    async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE, params=params):
        if event.type == WatchEventType.BOOKMARK:
            continue
        print(f"  {event.type}: {event.object.metadata.name}")


async def watch_with_changes():
    """Watch while making changes to see live events."""

    async def watcher():
        async for event in watch_k8s_resources(ConfigMap, namespace=NAMESPACE):
            if event.type == WatchEventType.BOOKMARK:
                continue
            name = event.object.metadata.name
            if name.startswith("watch-test"):
                print(f"  Event: {event.type} {name}")

    async def make_changes():
        await asyncio.sleep(0.5)
        for i in range(3):
            cm = ConfigMap(
                metadata=ObjectMeta(name=f"watch-test-{i}", namespace=NAMESPACE),
                data={"index": str(i)},
            )
            await create_k8s_resource(cm)
            print(f"  Created: watch-test-{i}")
            await asyncio.sleep(0.3)

        await asyncio.sleep(0.5)
        for i in range(3):
            await delete_k8s_resource(
                ConfigMap, name=f"watch-test-{i}", namespace=NAMESPACE,
                return_api_exceptions=[404]
            )

    await asyncio.gather(
        asyncio.wait_for(watcher(), timeout=10),
        make_changes(),
    )


async def main():
    await login()
    await basic_watch()
    await watch_with_changes()


asyncio.run(main())
```

### Watch Event Types

| Event Type | Description |
|------------|-------------|
| `ADDED` | Resource was created (also sent for existing resources when watch starts) |
| `MODIFIED` | Resource was updated |
| `DELETED` | Resource was deleted |
| `BOOKMARK` | Checkpoint for watch resumption (skip in most cases) |
| `ERROR` | Watch error occurred |

---

## Patching and Updates

Different strategies for updating Kubernetes resources.

### Understanding Frozen Dataclasses

kubesdk uses frozen (immutable) dataclasses. Use `dataclasses.replace()` to create modified copies:

```python
from dataclasses import replace

# WRONG - will raise FrozenInstanceError
# fetched.metadata.labels["new"] = "value"

# CORRECT - create new instances from innermost to outermost
new_labels = dict(fetched.metadata.labels)
new_labels["new"] = "value"

new_metadata = replace(fetched.metadata, labels=new_labels)
modified = replace(fetched, metadata=new_metadata)
```

### Strategic Merge Patch (Default)

Kubernetes-native patching that intelligently merges changes:

```python
from dataclasses import replace
from kubesdk import get_k8s_resource, update_k8s_resource
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap

# Fetch current state
original = await get_k8s_resource(ConfigMap, name="my-config", namespace="default")

# Modify (using replace for frozen dataclasses)
new_labels = dict(original.metadata.labels or {})
new_labels["environment"] = "production"
new_labels["version"] = "v2"

new_data = dict(original.data or {})
new_data["new-key"] = "new-value"

new_metadata = replace(original.metadata, labels=new_labels)
modified = replace(original, metadata=new_metadata, data=new_data)

# Update - kubesdk computes diff and sends only changes
updated = await update_k8s_resource(modified, built_from_latest=original)
```

### Selective Field Updates with PathPicker

Update only specific fields, ignoring other changes:

```python
from kubesdk import from_root_, path_

original = await get_k8s_resource(ConfigMap, name="my-config", namespace="default")

# Make multiple modifications
new_labels = dict(original.metadata.labels or {})
new_labels["selective"] = "update"

new_annotations = dict(original.metadata.annotations or {})
new_annotations["note"] = "this won't be updated"

new_data = dict(original.data or {})
new_data["key"] = "this also won't be updated"

new_metadata = replace(original.metadata, labels=new_labels, annotations=new_annotations)
modified = replace(original, metadata=new_metadata, data=new_data)

# Only update labels - ignore annotations and data changes
cm = from_root_(ConfigMap)
updated = await update_k8s_resource(
    modified,
    built_from_latest=original,
    paths=[path_(cm.metadata.labels)],  # only this field is updated
)
```

### Update Specific Dictionary Keys

```python
cm = from_root_(ConfigMap)
updated = await update_k8s_resource(
    modified,
    built_from_latest=original,
    paths=[
        path_(cm.data["specific-key"]),  # only update this key in data
    ],
)
```

### Full Replacement (PUT)

Replace entire resource instead of patching:

```python
# Create completely new resource with same name
replacement = ConfigMap(
    metadata=ObjectMeta(
        name="my-config",
        namespace="default",
        labels={"app": "replaced"},
        resourceVersion=original.metadata.resourceVersion,  # required for PUT
    ),
    data={"completely": "new", "config": "data"},
)

# force=True uses PUT instead of PATCH
updated = await update_k8s_resource(replacement, force=True)
```

### Handling Conflicts

```python
from kubesdk import ConflictError

try:
    await update_k8s_resource(stale_resource, force=True)
except ConflictError:
    # Fetch fresh version and retry
    fresh = await get_k8s_resource(ConfigMap, name="my-config", namespace="default")

    new_data = dict(fresh.data)
    new_data["retried"] = "success"
    modified = replace(fresh, data=new_data)

    await update_k8s_resource(modified, built_from_latest=fresh)
```

---

## Multi-Cluster Operations

Working with multiple Kubernetes clusters.

```python
import asyncio
from kubesdk.login import login, KubeConfig
from kubesdk import create_k8s_resource, get_k8s_resource, delete_k8s_resource
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap, Namespace
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

NAMESPACE = "default"


async def main():
    # Connect to multiple clusters
    clusters = {}

    # Primary cluster (default context)
    server_info = await login()
    clusters["primary"] = server_info.server
    print(f"Connected to primary: {server_info.server}")

    # Secondary cluster (specific context)
    server_info = await login(KubeConfig(context_name="staging"))
    clusters["secondary"] = server_info.server
    print(f"Connected to secondary: {server_info.server}")

    # OPERATIONS ON SPECIFIC CLUSTER
    # Use server= parameter to target a cluster

    cm = ConfigMap(
        metadata=ObjectMeta(
            name="multi-cluster-test",
            namespace=NAMESPACE,
        ),
        data={"cluster": "primary"},
    )

    # Create on primary
    created = await create_k8s_resource(cm, server=clusters["primary"])
    print(f"Created on primary: {created.metadata.name}")

    # Read from primary
    fetched = await get_k8s_resource(
        ConfigMap,
        name="multi-cluster-test",
        namespace=NAMESPACE,
        server=clusters["primary"],
    )
    print(f"Fetched from primary: {fetched.data}")

    # Delete from primary
    await delete_k8s_resource(
        ConfigMap,
        name="multi-cluster-test",
        namespace=NAMESPACE,
        server=clusters["primary"],
    )

    # PARALLEL QUERIES ACROSS CLUSTERS
    async def count_namespaces(name: str, server: str) -> tuple[str, int]:
        namespaces = await get_k8s_resource(Namespace, server=server)
        return name, len(namespaces.items)

    results = await asyncio.gather(*[
        count_namespaces(name, server)
        for name, server in clusters.items()
    ])

    print("Namespace counts:")
    for name, count in results:
        print(f"  {name}: {count}")


asyncio.run(main())
```

---

## Error Handling

Comprehensive error management patterns.

### Typed Exceptions

```python
from kubesdk import (
    get_k8s_resource,
    create_k8s_resource,
    NotFoundError,
    ConflictError,
    BadRequestError,
    RESTAPIError,
)
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import Status
```

### Catch NotFoundError (404)

```python
try:
    await get_k8s_resource(ConfigMap, name="does-not-exist", namespace="default")
except NotFoundError as e:
    print(f"NotFoundError: status={e.status}")
    if e.extra and isinstance(e.extra, Status):
        print(f"  reason={e.extra.reason}, message={e.extra.message}")
```

### Catch ConflictError (409)

```python
try:
    await create_k8s_resource(cm)
    await create_k8s_resource(cm)  # duplicate - will conflict
except ConflictError as e:
    print(f"ConflictError: status={e.status}")
    if e.extra:
        print(f"  reason={e.extra.reason}")
```

### Catch Validation Errors (400/422)

```python
invalid_cm = ConfigMap(
    metadata=ObjectMeta(name="invalid/name", namespace="default"),  # invalid name
    data={"key": "value"},
)

try:
    await create_k8s_resource(invalid_cm)
except (BadRequestError, RESTAPIError) as e:
    print(f"ValidationError: status={e.status}")
    if e.extra and hasattr(e.extra, 'message'):
        print(f"  message={e.extra.message}")
```

### Return Errors Instead of Raising

Use `return_api_exceptions` for cleaner control flow:

```python
result = await get_k8s_resource(
    ConfigMap,
    name="might-not-exist",
    namespace="default",
    return_api_exceptions=[404],  # return error instead of raising
)

if isinstance(result, RESTAPIError):
    print(f"Returned error: {type(result).__name__}, status={result.status}")
else:
    print(f"Found: {result.metadata.name}")
```

### Batch Error Aggregation

```python
names = ["config-1", "config-2", "config-3"]
results = {"found": [], "not_found": []}

for name in names:
    result = await get_k8s_resource(
        ConfigMap,
        name=name,
        namespace="default",
        return_api_exceptions=[404],
    )

    if isinstance(result, NotFoundError):
        results["not_found"].append(name)
    else:
        results["found"].append(name)

print(f"Found: {len(results['found'])}, Not found: {len(results['not_found'])}")
```

### Exception Reference

| Status Code | Exception Class | Description |
|-------------|-----------------|-------------|
| 400 | `BadRequestError` | Invalid request syntax |
| 401 | `UnauthorizedError` | Authentication required |
| 403 | `ForbiddenError` | Permission denied |
| 404 | `NotFoundError` | Resource not found |
| 409 | `ConflictError` | Resource already exists or version conflict |
| 422 | `UnprocessableEntityError` | Validation failed |
| 500 | `InternalServerError` | Server error |
| 503 | `ServiceUnavailableError` | Service unavailable |

---

## Batch Operations

Efficient bulk operations using asyncio.

### Parallel Create

```python
import asyncio
import time
from kubesdk import create_k8s_resource, delete_k8s_resource, get_k8s_resource
from kubesdk.client import K8sQueryParams, QueryLabelSelector

BATCH_LABEL = {"batch-example": "true"}


async def parallel_create(count: int = 10) -> int:
    """Create multiple ConfigMaps in parallel."""
    resources = [
        ConfigMap(
            metadata=ObjectMeta(
                name=f"batch-cm-{i}",
                namespace="default",
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
```

### Batch Update by Label

```python
async def batch_update_by_label() -> int:
    """Update all ConfigMaps matching a label selector."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels=BATCH_LABEL)
    )

    configmaps = await get_k8s_resource(ConfigMap, namespace="default", params=params)
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
```

### Batch Delete

```python
async def batch_delete() -> int:
    """Delete all ConfigMaps matching label selector."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels=BATCH_LABEL)
    )

    configmaps = await get_k8s_resource(ConfigMap, namespace="default", params=params)
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
```

### Semaphore for Rate Limiting

```python
async def rate_limited_operations(max_concurrent: int = 5):
    """Limit concurrent operations to avoid overwhelming API server."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def create_with_limit(resource):
        async with semaphore:
            return await create_k8s_resource(resource)

    resources = [ConfigMap(...) for i in range(100)]
    results = await asyncio.gather(
        *[create_with_limit(r) for r in resources],
        return_exceptions=True,
    )
```

---

## Custom Resources (CRDs)

Working with Custom Resource Definitions.

This example uses [CloudNativePG](https://cloudnative-pg.io/) - a popular PostgreSQL operator.

### Prerequisites

Install CloudNativePG operator:
```bash
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/releases/cnpg-1.25.0.yaml
```

### Define Custom Resource Class

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import ClassVar, Dict  # Dict needed for lazy loading

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
vars(kube_models)["__ALL_RESOURCES"][(Cluster.apiVersion, Cluster.kind)] = Cluster
```

### Required Class Attributes

| Attribute | Description | Example |
|-----------|-------------|---------|
| `apiVersion` | API group and version | `"postgresql.cnpg.io/v1"` |
| `kind` | Resource kind | `"Cluster"` |
| `group_` | API group | `"postgresql.cnpg.io"` |
| `plural_` | Plural resource name | `"clusters"` |
| `api_path_` | API endpoint path | `"apis/.../namespaces/{namespace}/clusters"` |
| `patch_strategies_` | Supported patch types | `{PatchRequestType.merge}` |

### CRUD Operations

```python
async def main():
    await login()

    # CREATE
    cluster = Cluster(
        metadata=ObjectMeta(name="example-pg", namespace=NAMESPACE),
        spec={
            "instances": 1,
            "storage": {"size": "1Gi"},
        },
    )
    created = await create_k8s_resource(cluster)
    print(f"Created: {created.metadata.name}")

    # READ
    fetched = await get_k8s_resource(Cluster, name="example-pg", namespace=NAMESPACE)
    print(f"Fetched: {fetched.metadata.name}, instances={fetched.spec.get('instances')}")

    # UPDATE
    updated_cluster = Cluster(
        metadata=fetched.metadata,
        spec={**fetched.spec, "instances": 2},
    )
    updated = await update_k8s_resource(updated_cluster, built_from_latest=fetched)
    print(f"Updated: instances={updated.spec.get('instances')}")

    # DELETE
    await delete_k8s_resource(Cluster, name="example-pg", namespace=NAMESPACE)
    print("Deleted")


asyncio.run(main())
```

### Generate Models with kubesdk-cli

For production use, generate fully typed models from your cluster:

```bash
kubesdk --url https://your-cluster:6443 \
        --output ./models \
        --module-name models \
        --http-headers "Authorization: Bearer TOKEN"
```

This generates typed dataclasses for all CRDs installed in your cluster, with full IDE autocomplete support.

---

## Troubleshooting

### Connection Issues
```bash
# Verify kubeconfig
kubectl config current-context
kubectl cluster-info
```

### Permission Errors (403)
- Check RBAC permissions for your user/service account
- Verify namespace access

### Conflict Errors (409)
- Use `built_from_latest` for optimistic concurrency
- Implement retry logic with fresh fetch

### Not Found (404)
- Verify resource name and namespace
- Check if CRD is installed: `kubectl get crd`
