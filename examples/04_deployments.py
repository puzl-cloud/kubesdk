#!/usr/bin/env python3
"""
kubesdk Deployment Management Example

Demonstrates Deployment lifecycle operations:
- Creating Deployments with resource specs
- Scaling replicas
- Rolling image updates
- Cleanup with propagation policy

Prerequisites:
- A valid kubeconfig at ~/.kube/config or running inside a Kubernetes pod
- Access to create/manage Deployments in the 'default' namespace
"""

import asyncio
from dataclasses import replace

from kubesdk.login import login
from kubesdk import (
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
)
from kubesdk.client import K8sQueryParams, QueryLabelSelector
from kubesdk.path_picker import from_root_, path_

from kube_models.api_v1.io.k8s.api.core.v1 import (
    Container,
    ContainerPort,
    EnvVar,
    PodSpec,
    PodTemplateSpec,
    ResourceRequirements,
)
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import (
    ObjectMeta,
    LabelSelector,
)
from kube_models.apis_apps_v1.io.k8s.api.apps.v1 import (
    Deployment,
    DeploymentSpec,
    DeploymentStrategy,
    RollingUpdateDeployment,
)

NAMESPACE = "default"
DEPLOYMENT_NAME = "kubesdk-demo-app"
APP_LABEL = "kubesdk-demo"


def create_deployment_spec(
    name: str,
    image: str,
    replicas: int = 2,
    port: int = 8080,
) -> Deployment:
    """Create a Deployment with resource requests/limits and rolling update strategy."""
    labels = {"app": APP_LABEL, "version": "v1"}

    return Deployment(
        metadata=ObjectMeta(
            name=name,
            namespace=NAMESPACE,
            labels=labels,
        ),
        spec=DeploymentSpec(
            replicas=replicas,
            selector=LabelSelector(matchLabels={"app": APP_LABEL}),
            strategy=DeploymentStrategy(
                type="RollingUpdate",
                rollingUpdate=RollingUpdateDeployment(
                    maxSurge="25%",
                    maxUnavailable="25%",
                ),
            ),
            template=PodTemplateSpec(
                metadata=ObjectMeta(labels=labels),
                spec=PodSpec(
                    containers=[
                        Container(
                            name="app",
                            image=image,
                            ports=[ContainerPort(containerPort=port)],
                            resources=ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "500m", "memory": "256Mi"},
                            ),
                            env=[
                                EnvVar(name="PORT", value=str(port)),
                            ],
                        )
                    ],
                ),
            ),
        ),
    )


async def create_deployment() -> Deployment:
    """Create a new Deployment and return the created resource."""
    deployment = create_deployment_spec(
        name=DEPLOYMENT_NAME,
        image="nginx:1.24",
        replicas=2,
    )
    created = await create_k8s_resource(deployment)
    print(f"Created Deployment: {created.metadata.name}")
    print(f"  Replicas: {created.spec.replicas}")
    print(f"  Image: {created.spec.template.spec.containers[0].image}")
    return created


async def get_deployment_status() -> Deployment:
    """Fetch and display current Deployment status."""
    deployment = await get_k8s_resource(
        Deployment,
        name=DEPLOYMENT_NAME,
        namespace=NAMESPACE,
    )

    status = deployment.status
    print(f"Deployment: {deployment.metadata.name}")
    print(f"  Desired: {deployment.spec.replicas}")
    print(f"  Ready: {status.readyReplicas or 0}")
    print(f"  Available: {status.availableReplicas or 0}")
    print(f"  Updated: {status.updatedReplicas or 0}")

    if status.conditions:
        for condition in status.conditions:
            print(f"  Condition: {condition.type}={condition.status}")

    return deployment


async def scale_deployment(replicas: int) -> Deployment:
    """
    Scale Deployment to specified replicas using selective field patching.

    Uses path_picker to update only the replicas field, minimizing
    the patch payload and avoiding conflicts on other fields.
    """
    deployment = await get_k8s_resource(
        Deployment,
        name=DEPLOYMENT_NAME,
        namespace=NAMESPACE,
    )

    # Frozen dataclass requires replace() for modifications
    new_spec = replace(deployment.spec, replicas=replicas)
    modified = replace(deployment, spec=new_spec)

    # Patch only the replicas field
    obj = from_root_(Deployment)
    updated = await update_k8s_resource(
        modified,
        paths=[path_(obj.spec.replicas)],
    )

    print(f"Scaled to {updated.spec.replicas} replicas")
    return updated


async def update_container_image(new_image: str) -> Deployment:
    """
    Update container image to trigger a rolling update.

    Uses built_from_latest for diff-based patching with conflict detection.
    """
    original = await get_k8s_resource(
        Deployment,
        name=DEPLOYMENT_NAME,
        namespace=NAMESPACE,
    )

    # Replace nested frozen dataclasses from innermost to outermost
    old_container = original.spec.template.spec.containers[0]
    new_container = replace(old_container, image=new_image)
    new_containers = [new_container] + list(original.spec.template.spec.containers[1:])
    new_pod_spec = replace(original.spec.template.spec, containers=new_containers)
    new_template = replace(original.spec.template, spec=new_pod_spec)
    new_spec = replace(original.spec, template=new_template)
    modified = replace(original, spec=new_spec)

    # Diff-based patch detects conflicts if resource changed since fetch
    updated = await update_k8s_resource(
        modified,
        built_from_latest=original,
    )

    print(f"Updated image: {updated.spec.template.spec.containers[0].image}")
    return updated


async def list_deployments_by_label() -> list[Deployment]:
    """List Deployments matching a label selector."""
    params = K8sQueryParams(
        labelSelector=QueryLabelSelector(matchLabels={"app": APP_LABEL})
    )

    deployment_list = await get_k8s_resource(
        Deployment,
        namespace=NAMESPACE,
        params=params,
    )

    print(f"Found {len(deployment_list.items)} deployment(s) with app={APP_LABEL}")
    for d in deployment_list.items:
        print(f"  - {d.metadata.name}: {d.spec.replicas} replicas")

    return deployment_list.items


async def cleanup_deployment() -> None:
    """Delete Deployment. Uses return_api_exceptions to handle already-deleted case."""
    result = await delete_k8s_resource(
        Deployment,
        name=DEPLOYMENT_NAME,
        namespace=NAMESPACE,
        return_api_exceptions=[404],
    )

    print(f"Deleted: {DEPLOYMENT_NAME}")


async def main():
    """Run deployment management examples."""
    await login()

    try:
        # Create
        await create_deployment()
        await asyncio.sleep(2)

        # Status
        await get_deployment_status()

        # List by label
        await list_deployments_by_label()

        # Scale up
        await scale_deployment(replicas=3)
        await asyncio.sleep(5)
        await get_deployment_status()

        # Rolling update
        await update_container_image("nginx:1.25")
        await asyncio.sleep(5)
        await get_deployment_status()

        # Scale down
        await scale_deployment(replicas=1)

    finally:
        await cleanup_deployment()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
