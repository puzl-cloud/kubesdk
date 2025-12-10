import logging

if not logging.getLogger().hasHandlers():
    class _DefaultFormatter(logging.Formatter):
        def format(self, record):
            s = super().format(record)
            base = set(logging.makeLogRecord({}).__dict__)
            base.add("message")
            extras = [f"{k}={v}" for k, v in record.__dict__.items() if k not in base]
            return s + (" | " + " ".join(extras) if extras else "")

    __handler = logging.StreamHandler()
    __handler.setFormatter(_DefaultFormatter("%(name)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(__handler)
    logging.getLogger().setLevel(logging.INFO)

logger = logging.getLogger(__name__)


from ._path.picker import PathPicker, PathRoot, path_, from_root_
from ._path.replace_at_path import replace_
from ._patch.json_patch import guard_lists_from_json_patch_replacement, apply_patch, json_patch_from_diff
from ._patch.strategic_merge_patch import jsonpatch_to_smp

from .login import login, KubeConfig
from .credentials import ServerInfo, ClientInfo, ConnectionInfo

from .errors import *
from .client import APIRequestProcessingConfig, APIRequestLoggingConfig, DryRun, PropagationPolicy, LabelSelectorOp, \
    QueryLabelSelectorRequirement, QueryLabelSelector, FieldSelectorOp, FieldSelectorRequirement, FieldSelector, \
    K8sQueryParams, K8sAPIRequestLoggingConfig, get_k8s_resource, create_k8s_resource, update_k8s_resource, \
    delete_k8s_resource, create_or_update_k8s_resource, WatchEventType, K8sResourceEvent, watch_k8s_resources
