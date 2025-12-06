#!/usr/bin/env python3
"""
kubesdk Authentication Example

This example demonstrates various authentication methods and patterns:
- Default login (auto-detects credentials)
- Specific kubeconfig context
- Custom kubeconfig path
- Service account authentication (in-cluster)
- Multi-cluster authentication

kubesdk automatically handles:
- Token refresh
- Credential rotation
- Re-authentication on 401 errors
- Connection pooling per cluster
"""

import asyncio
import os

from kubesdk.login import login, KubeConfig
from kubesdk import get_k8s_resource

from kube_models.api_v1.io.k8s.api.core.v1 import Namespace


async def default_login():
    """
    Default login - automatically detects credentials.

    Priority order:
    1. Service account (if running in-cluster)
    2. kubeconfig from KUBECONFIG env var
    3. kubeconfig from ~/.kube/config
    """
    server_info = await login()

    # Verify connection by listing namespaces (inspect namespaces.items as needed)
    namespaces = await get_k8s_resource(Namespace)

    return server_info


async def login_with_specific_context():
    """
    Login using a specific context from kubeconfig.

    Useful when your kubeconfig has multiple clusters and you want
    to explicitly choose which one to connect to.
    """
    # Set the desired kubeconfig context (kubectl config get-contexts)
    kubeconfig = KubeConfig(context_name="my-production-cluster")

    try:
        server_info = await login(kubeconfig)
        return server_info
    except Exception:
        return None


async def login_with_custom_kubeconfig_path():
    """
    Login using a kubeconfig file from a custom location.

    Useful for:
    - CI/CD pipelines with generated configs
    - Development with multiple config files
    - Testing different cluster configurations
    """
    custom_path = os.path.expanduser("~/.kube/puzl-gitlab-config")

    kubeconfig = KubeConfig(
        path=custom_path
    )

    server_info = await login(kubeconfig)
    return server_info


async def login_multiple_clusters():
    """
    Connect to multiple Kubernetes clusters simultaneously.

    Each cluster gets its own connection pool and credential management.
    You can specify which cluster to use for each operation via the
    `server` parameter.
    """
    clusters = {}

    # Default cluster used when operations omit the server parameter
    try:
        clusters["default"] = await login()
    except Exception:
        # Ignore failures here so we can keep trying other contexts in the demo
        pass

    additional_contexts = ["staging", "production"]

    for context_name in additional_contexts:
        try:
            kubeconfig = KubeConfig(context_name=context_name)
            server_info = await login(kubeconfig, use_as_default=False)
            clusters[context_name] = server_info
        except Exception:
            pass

    return clusters


async def check_connection_info():
    """
    Check current connection information.
    """
    server_info = await login()
    # Inspect server_info.* for cluster URL, CA path, and TLS verification flags


async def main():
    """Run authentication examples."""
    # Basic default login (most common case)
    await default_login()

    # Check connection details
    await check_connection_info()

    # Uncomment to test other patterns:
    # await login_with_specific_context()
    # await login_with_custom_kubeconfig_path()
    # await login_multiple_clusters()


if __name__ == "__main__":
    asyncio.run(main())
