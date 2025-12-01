import logging

if not logging.getLogger().hasHandlers():
    class _DefaultFormatter(logging.Formatter):
        def format(self, record):
            s = super().format(record)
            base = set(logging.makeLogRecord({}).__dict__)
            base.add("message")
            extras = [f"{k}={v}" for k, v in record.__dict__.items() if k not in base]
            return s + (" | " + " ".join(extras) if extras else "")

    handler = logging.StreamHandler()
    handler.setFormatter(_DefaultFormatter("%(name)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


logger = logging.getLogger(__name__)

from .client import create_k8s_resource, get_k8s_resource, update_k8s_resource, delete_k8s_resource, \
    create_or_update_k8s_resource, APIRequestProcessingConfig, APIRequestLoggingConfig, K8sAPIRequestLoggingConfig
from .errors import *
