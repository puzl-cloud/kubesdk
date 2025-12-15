import os
import logging
import asyncio
from dataclasses import fields, dataclass, field

import yaml

from .common import normalize_dict_keys
from .errors import *
from .credentials import ConnectionInfo, ServerInfo, ClientInfo, Vault
from ._auth import _auth_vault_var, DEFAULT_VAULT_NAME
from .common import host_from_url, join_host_port


_log = logging.getLogger(__name__)

SERVICE_HOST_ENV_NAME = "KUBERNETES_SERVICE_HOST"
SERVICE_PORT_ENV_NAME = "KUBERNETES_SERVICE_PORT"
KUBE_CONFIG_DEFAULT_LOCATION = os.getenv("KUBECONFIG", "~/.kube/config")
KUBECONFIG_DEFAULT_CONTEXT = "default"


@dataclass(kw_only=True, frozen=True)
class KubeConfig:
    # We separate default-config and no-config cases. We load current context if context_name is None.
    context_name: str = field(default=None)
    path: str = field(default=KUBE_CONFIG_DEFAULT_LOCATION)


def _connection_info_from_kube_config(kubeconfig: KubeConfig) -> ConnectionInfo | None:
    # As per https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/
    if not os.path.exists(os.path.expanduser(kubeconfig.path)):
        return None

    paths = [path.strip() for path in kubeconfig.path.split(os.pathsep)]
    paths = [os.path.expanduser(path) for path in paths if path]

    # As prescribed: if the file is absent or wrong-formatted, then fail. The first value wins.
    current_context = None
    contexts, clusters, users = {}, {}, {}
    for path in paths:
        with open(path, "rt", encoding="utf-8") as f:
            config = yaml.safe_load(f.read()) or {}

        current_context = current_context or config.get("current-context")
        for item in config.get("contexts", []):
            if item["name"] not in contexts:
                contexts[item["name"]] = item.get("context") or {}
        for item in config.get("clusters", []):
            if item["name"] not in clusters:
                clusters[item["name"]] = item.get("cluster") or {}
        for item in config.get("users", []):
            if item["name"] not in users:
                users[item["name"]] = item.get("user") or {}

    if kubeconfig.context_name:
        if kubeconfig.context_name not in contexts:
            raise LoginError(f"Context with name `{kubeconfig.context_name}` is not found in kubeconfig")
        use_context_name = kubeconfig.context_name
    elif current_context:
        use_context_name = current_context
    elif KUBECONFIG_DEFAULT_CONTEXT in contexts:
        use_context_name = KUBECONFIG_DEFAULT_CONTEXT
    else:
        raise LoginError(
            "`current-context` key is not set in kubeconfig, and no `default` context was found. "
            "You should fix your kubeconfig file or provide valid `kubeconfig` argument to login function.")

    context = contexts[use_context_name]
    cluster = clusters[context["cluster"]]
    user = users[context["user"]]
    provider_token = ((user.get("auth-provider") or {}).get("config") or {}).get("access-token")
    
    normalized_cluster_data = normalize_dict_keys(cluster)
    normalized_client_data = normalize_dict_keys(user)
    client_info = {
        f.name: normalized_client_data[f.name] for f in fields(ClientInfo)
        if f.name in normalized_client_data
    }
    client_info["token"] = client_info.get("token") or provider_token

    # Map the retrieved fields into the credentials object.
    return ConnectionInfo(
        server_info=ServerInfo(**{
            f.name: normalized_cluster_data[f.name] for f in fields(ServerInfo)
            if f.name in normalized_cluster_data
        }),
        client_info=ClientInfo(**client_info),
        default_namespace=normalized_cluster_data.get("default_namespace"),
        priority=10,
        kubeconfig_context_name=use_context_name
    )


def _connection_info_from_service_account() -> ConnectionInfo | None:
    # As per https://kubernetes.io/docs/tasks/run-application/access-api-from-pod/
    root = "/var/run/secrets/kubernetes.io/serviceaccount"
    token_path, ns_path, ca_path = f"{root}/token", f"{root}/namespace", f"{root}/ca.crt"
    if not os.path.exists(token_path):
        _log.debug(f"{token_path} path does not exist, can not load service account config")
        return None

    host, port = os.getenv(SERVICE_HOST_ENV_NAME), os.getenv(SERVICE_PORT_ENV_NAME)
    if not host:
        _log.debug(f"{SERVICE_HOST_ENV_NAME} env is not set, can not load service account config")
        return None
    if not port:
        _log.debug(f"{SERVICE_PORT_ENV_NAME} env is not set, can not load service account config")
        return None

    with open(token_path, encoding="utf-8") as f:
        token = f.read().strip()

    namespace = None
    if os.path.exists(ns_path):
        with open(ns_path, encoding="utf-8") as f:
            namespace = f.read().strip()
    return ConnectionInfo(
        server_info=ServerInfo(
            server=f"https://{join_host_port(host, port)}",
            certificate_authority=ca_path if os.path.exists(ca_path) else None),
        client_info=ClientInfo(token=token or None),
        default_namespace=namespace or None,
        priority=20
    )


def _collect_connection_info(kubeconfig: KubeConfig = None) -> ConnectionInfo:
    sa_connection_info = _connection_info_from_service_account()
    if sa_connection_info and not kubeconfig:
        return sa_connection_info

    try:
        # Try to use default config, if not passed
        return _connection_info_from_kube_config(kubeconfig or KubeConfig())
    except LoginError:
        # If kubeconfig passed, it's required to use kubeconfig flow
        if kubeconfig or not sa_connection_info:
            raise
        return sa_connection_info


async def _sync_credentials(result: dict[str, ServerInfo], kubeconfig: KubeConfig = None, use_as_default: bool = None):
    """
    Keeping the credentials forever up to date
    """
    async def whoami() -> str:
        from .client import create_k8s_resource
        from kube_models.apis_authentication_k8s_io_v1.io.k8s.api.authentication.v1 import SelfSubjectReview

        subj = await create_k8s_resource(SelfSubjectReview(), server=connection_info.server_info.server)
        return subj.status.userInfo.username

    vaults = _auth_vault_var.get()
    backoff_timeout = 10
    if use_as_default and DEFAULT_VAULT_NAME in vaults:
        _log.warning(f"The default Kubernetes server is already defined. Check that you don't have undesired "
                     f"duplicated login() calls. `use_as_default` setting was ignored.")
        use_as_default = False

    # Check that vault does not exist for this server yet before falling into the loop
    connection_info = _collect_connection_info(kubeconfig)
    cluster_host = host_from_url(connection_info.server_info.server)
    existing_vault = vaults.get(cluster_host)
    if existing_vault is not None:
        vault_item = existing_vault.select()
        result["info"] = vault_item[1].info.server_info if vault_item else None
        return

    while True:
        try:
            connection_info = _collect_connection_info(kubeconfig)
            cluster_host = host_from_url(connection_info.server_info.server)
            vault = vaults.get(cluster_host)
            if vault is None:
                vault = vaults[cluster_host] = Vault()
                # Set the first config as default
                if use_as_default or (use_as_default is None and DEFAULT_VAULT_NAME not in vaults):
                    vaults[DEFAULT_VAULT_NAME] = vault

            await vault.wait_for_emptiness()
            await vault.populate({connection_info.kubeconfig_context_name or "ServiceAccount": connection_info})
            account = await whoami()
            result["info"] = connection_info.server_info
            _log.info(f"kubesdk client for `{cluster_host}` is running under `{account}` account")
        except Exception as e:
            if kubeconfig:
                log_context = kubeconfig.context_name or 'current'
                log_context = f"`{log_context}` credential context at kubeconfig {kubeconfig.path}"
            else:
                log_context = "ServiceAccount in-cluster credentials"
            _log.error(f"Syncing {log_context} failed and will be retried in {backoff_timeout} seconds. "
                       f"Unexpected error: {e}")
            await asyncio.sleep(backoff_timeout)


async def login(kubeconfig: KubeConfig = None, use_as_default: bool = None) -> ServerInfo:
    server_info: dict[str, ServerInfo] = {}
    asyncio.create_task(_sync_credentials(server_info, kubeconfig, use_as_default))

    # Was too lazy to wait this better
    timer = 0
    while not server_info.get("info"):
        await asyncio.sleep(1)
        timer += 1
        if timer > 35:
            msg = "kubesdk login timed out. Check your client environment and Kubernetes server status."
            _log.error(msg)
            raise LoginError(msg)
    return server_info["info"]
