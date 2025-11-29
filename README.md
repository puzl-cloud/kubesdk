
[![kubesdk](https://img.shields.io/pypi/v/kubesdk.svg?label=kubesdk)](https://pypi.org/project/kubesdk)
[![kube-models](https://img.shields.io/pypi/v/kube-models.svg?label=kube-models)](https://pypi.org/project/kube-models)
[![kubesdk-cli](https://img.shields.io/pypi/v/kubesdk-cli.svg?label=kubesdk-cli)](https://pypi.org/project/kubesdk-cli)
[![python versions](https://img.shields.io/pypi/pyversions/kubesdk.svg)](https://pypi.org/project/kubesdk)
[![coverage](https://img.shields.io/coverallsCoverage/github/puzl-cloud/kubesdk?label=coverage)](https://coveralls.io/github/puzl-cloud/kubesdk)
[![actions status](https://github.com/puzl-cloud/kubesdk/actions/workflows/publish.yml/badge.svg)](https://github.com/puzl-cloud/kubesdk/actions/workflows/publish.yml)

# kubesdk

`kubesdk` is a modern, async-first Kubernetes client and API model generator for Python.
- Developer-friendly, with fully typed APIs so IDE auto-complete works reliably across built-in resources and your custom resources. 
- Made for large multi-cluster workloads.  
- Minimal external dependencies (client itself depends on `aiohttp` and `PyYAML` only).

The project is split into three packages:

## `kubesdk`

The core client library, which you install and use in your project.

## `kube-models`

Pre-generated Python models for all upstream Kubernetes APIs, for every Kubernetes version **1.23+**. Separate models package gives you ability to use latest client version with legacy Kubernetes APIs and vice versa.

You can find the latest generated models [here](https://github.com/puzl-cloud/kube-models). They are automatically uploaded to an external repository to avoid increasing the size of the main `kubesdk` repo.

## `kubesdk-cli`

CLI that generates models from a live cluster or OpenAPI spec, including your own CRDs.

## Comparison with other Python clients

| Feature / Library                  | **kubesdk** | kubernetes-asyncio | Official client (`kubernetes`) | kr8s     | lightkube |
|------------------------------------|-------------|-------------------|------------------------------|----------|----------|
| Async client                       | ✅           | ✅                 | ✗                            | ✅        | ✅        |
| IDE-friendly client methods typing | ✅ Full      | ◑ Partial         | ◑ Partial                    | ◑ Partial | ✅ Good   |
| Typed models for all built-in APIs | ✅           | ✅                 | ✅                            | ◑ Partial | ✅        |
| Built-in multi-cluster ergonomics  | ✅           | ◑ Manual          | ◑ Manual                     | ◑ Manual | ◑ Manual |
| Easy API model generation (CLI)    | ✅           | ✗                 | ✗                            | ◑        | ◑        |
| High-level JSON Patch helpers (typed)      | ✅           | ✗                 | ✗                            | ✗        | ✗        |
| One API surface for core + CRDs    | ✅           | ✗                 | ✗                            | ◑        | ✅        |
| Separated API models package       | ✅           | ✗                 | ✗                            | ✗        | ✅        |
| Performance on large-scale workloads | ✅ >1000 RPS | ✅ >1000 RPS       | <100 RPS                     | <100 RPS | <100 RPS |

## Installation

```bash
pip install kubesdk[cli]
```

## Quick examples

### Create and read resource

```python
import asyncio

from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import (
    Deployment,
    DeploymentSpec,
    LabelSelector,
)
from kube_models.api_v1.io.k8s.api.core.v1 import (
    PodTemplateSpec,
    PodSpec,
    Container,
)
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta

from kubesdk.login import login
from kubesdk.client import create_k8s_resource, get_k8s_resource


async def main() -> None:
    # Load available cluster config and establish cluster connection process-wide
    await login()

    deployment = Deployment(
        metadata=ObjectMeta(name="example-nginx", namespace="default"),
        spec=DeploymentSpec(
            replicas=2,
            selector=LabelSelector(matchLabels={"app": "example-nginx"}),
            template=PodTemplateSpec(
                metadata=ObjectMeta(labels={"app": "example-nginx"}),
                spec=PodSpec(
                    containers=[
                        Container(
                            name="nginx",
                            image="nginx:stable",
                        )
                    ]
                ),
            ),
        ),
    )

    # Create the Deployment
    await create_k8s_resource(deployment)

    # Read it back
    created = await get_k8s_resource(Deployment, "example-nginx", "default")
    
    # IDE autocomplete works here
    print("Container name:", created.spec.template.spec.containers[0].name)


if __name__ == "__main__":
    asyncio.run(main())
```

### Watch resources

```python
import asyncio

from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import Deployment
from kubesdk.login import login
from kubesdk.client import watch_k8s_resources


async def main() -> None:
    await login()

    async for event in watch_k8s_resources(Deployment, namespace="default"):
        deploy = event.object
        print(event.type, deploy.metadata.name)


if __name__ == "__main__":
    asyncio.run(main())
```

### Delete resources

```python
import asyncio

from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import Deployment
from kubesdk.login import login
from kubesdk.client import delete_k8s_resource


async def main() -> None:
    await login()
    await delete_k8s_resource(Deployment, "example-nginx", "default")


if __name__ == "__main__":
    asyncio.run(main())
```

### Patch resource

```python
from dataclasses import replace

from kube_models.api_v1.io.k8s.api.core.v1 import LimitRange, LimitRangeSpec, LimitRangeItem
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import OwnerReference, ObjectMeta

from kubesdk.client import create_k8s_resource, update_k8s_resource
from kubesdk.path_picker import from_root_, path_


async def patch_limit_range() -> None:
    """
    Example: bump PVC min storage and add an OwnerReference in a single,
    server-side patch. kubesdk will compute the diff between `latest` and
    `updated` and pick the best patch type (strategic/merge) automatically.
    """
    # Create the initial LimitRange object.
    namespace = "default"
    initial_range = LimitRange(
        metadata=ObjectMeta(
            name="example-limit-range",
            namespace=namespace,
        ),
        spec=LimitRangeSpec(
            limits=[
                LimitRangeItem(
                    type="PersistentVolumeClaim",
                    min={"storage": "1Gi"},
                )
            ]
        ),
    )

    # The client returns the latest version from the API server.
    latest: LimitRange = await create_k8s_resource(initial_range)

    # Build a new list of limits with updated PVC min storage.
    updated_limits = [
        replace(lim, min={"storage": "3Gi"})
        if lim.type == "PersistentVolumeClaim" else lim
        for lim in latest.spec.limits
    ]

    # Append a new OwnerReference.
    updated_range = replace(
        latest,
        metadata=replace(
            latest.metadata,
            ownerReferences=latest.metadata.ownerReferences + [
                OwnerReference(
                    uid="9153e39d-87d1-46b2-b251-5f6636c30610",
                    apiVersion="v1",
                    kind="Secret",
                    name="test-secret-1",
                ),
            ],
        ),
        spec=replace(latest.spec, limits=updated_limits),
    )

    update_all_changed_fields = True
    # Let kubesdk compute the diff and patch everything that changed
    if update_all_changed_fields:
        await update_k8s_resource(updated_range, built_from_latest=latest)

    # Or, restrict the patch to specific paths only (optional)
    else:
        obj = from_root_(LimitRange)
        await update_k8s_resource(
            updated_range,
            built_from_latest=latest,
            paths=[
                # IDE autocomplete works here
                path_(obj.metadata.ownerReferences),
                path_(obj.spec.limits),
            ],
        )
```

### Working with multiple clusters

```python
import asyncio
from dataclasses import replace

from kubesdk.login import login, KubeConfig, ServerInfo
from kubesdk.client import watch_k8s_resources, create_or_update_k8s_resource, delete_k8s_resource, WatchEventType
from kube_models.api_v1.io.k8s.api.core.v1 import Secret


async def sync_secrets_between_clusters(src_cluster: ServerInfo, dst_cluster: ServerInfo):
    src_ns, dst_ns = "default", "test-kubesdk"
    async for event in watch_k8s_resources(Secret, namespace=src_ns, server=src_cluster.server):
        if event.type == WatchEventType.DELETED:
            # Try to delete, skip if not found
            await delete_k8s_resource(Secret, namespace=dst_ns, server=dst_cluster.server, return_api_exceptions=[404])
            continue

        if event.type == WatchEventType.ERROR:
            status = event.object
            raise Exception(f"Failed to watch Secrets: {status.data}")

        # Optional
        if event.type == WatchEventType.BOOKMARK:
            continue

        # Sync Secret on any other event
        src_secret = event.object
        dst_secret = replace(
            src_secret,
            metadata=replace(src_secret.metadata, namespace=dst_ns,
                # Drop all k8s runtime fields
                uid=None,
                resourceVersion=None,
                managedFields=None))

        # If the Secret exists, a patch is applied; if it doesn't, it will be created.
        await create_or_update_k8s_resource(dst_secret, server=dst_cluster.server)
        print(f"Secret {dst_secret.metadata.name} has been synced "
              f"from `{src_ns}` ns in {src_cluster.server} to `{dst_ns}` ns in {dst_cluster.server}")


async def main():
    default = await login()
    eu_finland_1 = await login(kubeconfig=KubeConfig(context_name="eu-finland-1.clusters.puzl.cloud"))

    # Endless syncing loop
    while True:
        try:
            await sync_secrets_between_clusters(default, eu_finland_1)
        except Exception as e:
            print(e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
```

### CLI

Generate models directly from a live cluster OpenAPI:

```shell
kubesdk \
  --url https://my-cluster.example.com:6443 \
  --output ./kube_models \
  --module-name kube_models \
  --http-headers "Authorization: Bearer $(cat /path/to/token)" \
  --skip-tls
```

## Near-term roadmap

- [ ] Publish client benchmark suite and results
- [ ] Add contributor guide and contribution workflow
- [ ] Ship detailed API and usage documentation
- [ ] CRD YAML generator from your dataclasses
