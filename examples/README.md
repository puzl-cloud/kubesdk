# kubesdk Examples

Comprehensive examples demonstrating kubesdk features and best practices.

## Prerequisites

1. **Python 3.10+** with kubesdk installed:
   ```bash
   cd /path/to/kubesdk
   uv sync
   ```

2. **Kubernetes cluster access** via kubeconfig at `~/.kube/config`

3. **Permissions** to create/manage resources in the `default` namespace

## Running Examples

```bash
uv run python examples/01_quickstart.py
```

## Examples

| # | File | Description |
|---|------|-------------|
| 01 | quickstart.py | Basic CRUD operations with ConfigMaps |
| 02 | authentication.py | Login patterns: default, specific context, custom path |
| 03 | request_config.py | Timeouts, retries, and logging configuration |
| 04 | deployments.py | Deployment management: create, scale, rolling updates |
| 05 | watching.py | Real-time resource streaming with watch |
| 06 | patching.py | Strategic merge patch, JSON patch, selective updates |
| 07 | multi_cluster.py | Working with multiple clusters via server= parameter |
| 08 | error_handling.py | Typed exceptions, return_api_exceptions pattern |
| 09 | batch_operations.py | Parallel operations with asyncio.gather |
| 10 | custom_resources.py | CRD example with CloudNativePG Cluster |

## Key Patterns

### Basic CRUD
```python
from kubesdk.login import login
from kubesdk import create_k8s_resource, get_k8s_resource, delete_k8s_resource
from kube_models.api_v1.io.k8s.api.core.v1 import ConfigMap
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

async def main():
    await login()

    cm = ConfigMap(
        metadata=ObjectMeta(name="example", namespace="default"),
        data={"key": "value"}
    )
    await create_k8s_resource(cm)
    await get_k8s_resource(ConfigMap, name="example", namespace="default")
    await delete_k8s_resource(ConfigMap, name="example", namespace="default")
```

### Watch Resources
```python
from kubesdk.client import watch_k8s_resources, WatchEventType

async for event in watch_k8s_resources(ConfigMap, namespace="default"):
    if event.type == WatchEventType.ADDED:
        print(f"Created: {event.object.metadata.name}")
```

### Error Handling
```python
from kubesdk import NotFoundError

# Option 1: catch exception
try:
    await get_k8s_resource(ConfigMap, name="missing", namespace="default")
except NotFoundError as e:
    print(f"Not found: {e.extra.message}")

# Option 2: return error as value
result = await get_k8s_resource(
    ConfigMap, name="missing", namespace="default",
    return_api_exceptions=[404]
)
```

### Multi-Cluster
```python
from kubesdk.login import login, KubeConfig

primary = await login()
secondary = await login(KubeConfig(context_name="other-cluster"))

# Target specific cluster
await get_k8s_resource(ConfigMap, namespace="default", server=secondary.server)
```

### Batch Operations
```python
results = await asyncio.gather(
    *[create_k8s_resource(r) for r in resources],
    return_exceptions=True
)
```
