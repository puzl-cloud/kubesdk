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

from kubesdk import login, KubeConfig, ServerInfo


async def default_login() -> ServerInfo:
    """
    Default login - automatically detects credentials.

    Priority order:
    1. Service account (if running in-cluster)
    2. kubeconfig from KUBECONFIG env var
    3. kubeconfig from ~/.kube/config
    """
    server_info = await login()
    return server_info


async def login_with_specific_context() -> ServerInfo | None:
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


async def login_with_custom_kubeconfig_path() -> ServerInfo:
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


async def login_multiple_clusters() -> dict[str, ServerInfo]:
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


async def main():
    """Run authentication examples."""
    # Basic default login (most common case)
    await default_login()

    # Uncomment to test other patterns:
    # await login_with_specific_context()
    # await login_with_custom_kubeconfig_path()
    # await login_multiple_clusters()


if __name__ == "__main__":
    asyncio.run(main())
