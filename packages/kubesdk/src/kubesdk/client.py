from __future__ import annotations

import sys
import asyncio
import logging
import json
from enum import Enum
from typing import Type, Callable, overload, Literal, Sequence, Any, Mapping
from inspect import isclass
from dataclasses import dataclass, field, replace, fields
from collections.abc import AsyncIterator

if sys.version_info >= (3, 11):
    from http import HTTPMethod
else:
    class HTTPMethod(str, Enum):
        GET = "GET"
        POST = "POST"
        PUT = "PUT"
        PATCH = "PATCH"
        DELETE = "DELETE"
        HEAD = "HEAD"
        OPTIONS = "OPTIONS"

import aiohttp

from kube_models.loader import Loadable
from kube_models import get_model
from kube_models.const import PatchRequestType, StrEnum
from kube_models.resource import K8sResource, K8sResourceList
from kube_models.api_v1.io.k8s.apimachinery.pkg.apis.meta.v1 import DeleteOptions, Status

from ._auth import authenticated, APIContext
from .errors import *
from ._patch.strategic_merge_patch import jsonpatch_to_smp
from ._patch.json_patch import guard_lists_from_json_patch_replacement, json_patch_from_diff
from ._path.picker import PathPicker


_log = logging.getLogger(__name__)


@dataclass(kw_only=True)
class APIRequestProcessingConfig:
    http_timeout: int | None = field(default=None)
    backoff_limit: int = field(default=3)
    backoff_interval: int | Callable[[int], int] = field(default=5)
    retry_statuses: Sequence[int | Type[RESTAPIError]] = field(default_factory=list)


@dataclass(kw_only=True)
class APIRequestLoggingConfig:
    api_name: str
    on_success: bool = field(default=False)
    request_body: bool = field(default=False)
    response_body: Callable[[Any], bool] | bool = field(default=False)
    not_error_statuses: Sequence[int | Type[RESTAPIError]] = field(default_factory=list)

    def should_log_response(self, response: Any) -> bool:
        if callable(self.response_body):
            try:
                return self.response_body(response)
            except Exception:
                return False
        return self.response_body


class DryRun(str, Enum):
    All = "All"


class PropagationPolicy(str, Enum):
    Orphan = "Orphan"
    Background = "Background"
    Foreground = "Foreground"


class LabelSelectorOp(str, Enum):
    In = "In"
    NotIn = "NotIn"
    Exists = "Exists"
    DoesNotExist = "DoesNotExist"


@dataclass(kw_only=True, frozen=True)
class QueryLabelSelectorRequirement:
    key: str
    op: LabelSelectorOp
    values: Sequence[str] = field(default_factory=list)


@dataclass(kw_only=True, frozen=True)
class QueryLabelSelector:
    matchLabels: Mapping[str, str] = field(default_factory=dict)
    matchExpressions: Sequence[QueryLabelSelectorRequirement] = field(default_factory=list)

    def to_query_value(self) -> str:
        parts: list[str] = []
        for k, v in self.matchLabels.items():
            parts.append(f"{k}={v}")
        for expr in self.matchExpressions:
            op = expr.op
            if op is LabelSelectorOp.In:
                values = ",".join(expr.values)
                parts.append(f"{expr.key} in ({values})")
            elif op is LabelSelectorOp.NotIn:
                values = ",".join(expr.values)
                parts.append(f"{expr.key} notin ({values})")
            elif op is LabelSelectorOp.Exists:
                parts.append(expr.key)
            elif op is LabelSelectorOp.DoesNotExist:
                parts.append(f"!{expr.key}")
            else:
                raise ValueError(f"Unsupported QueryLabelSelector operator: {op}")
        return ",".join(parts)


class FieldSelectorOp(str, Enum):
    eq = "="
    neq = "!="


@dataclass(kw_only=True, frozen=True)
class FieldSelectorRequirement:
    # ToDo: Make `field` type of PathPicker to validate that the requested resource even have this field
    field: str
    op: FieldSelectorOp
    value: str


@dataclass(kw_only=True, frozen=True)
class FieldSelector:
    requirements: Sequence[FieldSelectorRequirement]
    def to_query_value(self) -> str: return ",".join(f"{r.field}{r.op.value}{r.value}" for r in self.requirements)


@dataclass(kw_only=True, frozen=True)
class K8sQueryParams:
    pretty: str | None = None
    _continue: str | None = None  # will be turned into `continue` on request
    fieldSelector: FieldSelector | None = None
    labelSelector: QueryLabelSelector | None = None
    limit: int | None = None
    resourceVersion: str | None = None
    timeoutSeconds: int | None = None
    watch: bool | None = None
    allowWatchBookmarks: bool | None = None
    gracePeriodSeconds: int | None = None
    propagationPolicy: PropagationPolicy | None = None
    dryRun: DryRun | None = None
    fieldManager: str | None = None
    force: bool | None = None

    def to_http_params(self) -> list[tuple[str, str]]:
        items = []
        for f in fields(self):
            name = f.name
            value = getattr(self, name)
            if value is None:
                continue

            if name == "_continue":
                items.append(("continue", str(value)))
                continue

            if isinstance(value, FieldSelector):
                items.append(("fieldSelector", value.to_query_value()))
                continue

            if isinstance(value, QueryLabelSelector):
                items.append(("labelSelector", value.to_query_value()))
                continue

            if isinstance(value, Enum):
                sval = value.value
            elif isinstance(value, bool):
                sval = "true" if value else "false"
            else:
                sval = str(value)

            items.append((name, sval))

        return items


@dataclass(kw_only=True)
class K8sAPIRequestLoggingConfig(APIRequestLoggingConfig):
    api_name: str = field(default="Kubernetes")
    response_body: Callable[[Any], bool] | bool = \
        field(default=lambda response_json: response_json.get("kind") == "Status")
    errors_as_critical: bool = field(default=False)


DEFAULT_PROCESSING = APIRequestProcessingConfig()
DEFAULT_LOGGING = K8sAPIRequestLoggingConfig()


async def _raw_api_request(
        method: HTTPMethod,
        url: str,
        session: aiohttp.ClientSession,
        *,
        data: dict[str, str | int | bool | list | dict] | list[dict[str, str | int | bool | list | dict]] = None,
        params: list[tuple[str, str]] = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: APIRequestLoggingConfig = DEFAULT_LOGGING
) -> aiohttp.ClientResponse:
    headers = headers or {}
    headers.setdefault("Accept", "application/json")
    api_name = log.api_name
    max_attempts = min(1, processing.backoff_limit)
    request_timeout = aiohttp.ClientTimeout(total=processing.http_timeout)
    extra_log = {
        "API": api_name,
        "url": url,
        "method": method,
        "content_type": headers.get("Content-Type") or headers.get("content-type")
    }
    if log.request_body:
        extra_log["request"] = data
    _log.debug(f"Requesting {api_name} API", extra=extra_log)
    attempt = 0

    while attempt < max_attempts:
        try:
            attempt += 1
            response = await session.request(
                method,
                url,
                params=params,
                headers=headers,
                json=data,
                timeout=request_timeout,
                allow_redirects=False,
                read_bufsize=None  # take from session
            )

            # Check if we have to retry forcibly
            exc_cls = ERROR_TYPE_BY_CODE.get(response.status)
            to_retry = processing.retry_statuses
            if (response.status in to_retry or exc_cls in to_retry) and attempt < max_attempts:
                _log.debug(
                    f"Retrying request due to {response.status} response status",
                    extra=extra_log | {"attempt": attempt, "status": response.status}
                )
                # Drain body so the connection can be reused
                try:
                    await response.read()
                finally:
                    response.release()
                if callable(processing.backoff_interval):
                    backoff = processing.backoff_interval(attempt)
                else:
                    backoff = processing.backoff_interval
                await asyncio.sleep(backoff)
                continue

            # We never parse response here because we don't know if it was stream or REST
            return response

        except asyncio.TimeoutError as exc:
            msg = f"API request failed by {request_timeout.total}sec timeout"
            msg = f"{msg}. Will be retried." if attempt < max_attempts else msg
            _log.error(msg, extra=extra_log | {"error": str(exc), "attempt": attempt})
            if attempt >= max_attempts:
                raise
        except aiohttp.ClientConnectorError as exc:
            _log.error("API request failed", extra=extra_log | {"error": str(exc), "attempt": attempt})
            raise RuntimeError(f"{api_name} API connection has been broken unexpectedly.")

    # For type-checkers
    raise RuntimeError("What are you doing here? Check the _raw_api_request() code!")


async def __load_aiohttp_response(response: aiohttp.ClientResponse) -> dict | list | str:
    try:
        return await response.json()
    except (json.JSONDecodeError, aiohttp.ContentTypeError):
        return await response.text()


@authenticated
async def rest_api_request(
        method: HTTPMethod,
        url: str,
        data: dict[str, str | int | bool | list | dict] | list[dict[str, str | int | bool | list | dict]] = None,
        *,
        params: list[tuple[str, str]] = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: APIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None,
        _context: APIContext = None
) -> dict | list | RESTAPIError:
    """Used for all non-watch requests."""
    headers, return_api_exceptions = headers or {}, return_api_exceptions or []
    headers.setdefault("Accept", "application/json")
    max_attempts = min(1, processing.backoff_limit)
    error_msg = f"{log.api_name} API request failed"
    success_msg = f"{log.api_name} API request has been processed"
    extra_log: dict[str, Any] = {
        "API": log.api_name,
        "url": url,
        "server": _context.server,
        "method": method
    }
    if log.request_body:
        extra_log["request"] = data
    _log.debug(f"Requesting {log.api_name} API", extra=extra_log)

    response = await _raw_api_request(
        method=method,
        url=url,
        session=_context.session,
        data=data,
        params=params,
        headers=headers,
        processing=processing,
        log=log
    )

    async with response:
        if response.status == 204:
            # It was DELETE or other non-body response
            return {}

        response_data = await __load_aiohttp_response(response)
        is_json = type(response_data) is not str
        extra_log["status"] = response.status
        if log.should_log_response(response_data):
            extra_log["response"] = response_data

        if not is_json:
            _log.error(f"Received non-JSON response from {log.api_name} API", extra=extra_log)
            response_data = {"data": response_data}

        # Check if we have to retry forcibly
        exc_cls = ERROR_TYPE_BY_CODE.get(response.status) or RESTAPIError
        if response.status in processing.retry_statuses or exc_cls in processing.retry_statuses:
            raise exc_cls(response.status, f"{error_msg}, max_attempts={max_attempts} reached", response_data)

        # Check if we have to return or raise
        if response.status >= 300:
            api_error = exc_cls(response.status, error_msg, response_data)
            extra_log["error"] = str(api_error.status)
            if response.status not in log.not_error_statuses and exc_cls not in log.not_error_statuses:
                _log_level = _log.error
            elif log.on_success:
                _log_level = _log.info
            else:
                _log_level = _log.debug
            _log_level(error_msg, extra=extra_log)
            if response.status in return_api_exceptions \
                    or any(isinstance(exc, RESTAPIError) and exc.status == response.status
                           for exc in return_api_exceptions):
                return api_error
            raise api_error

        if log.on_success:
            _log.info(success_msg, extra=extra_log)
        else:
            _log.debug(success_msg, extra=extra_log | {"response": response_data})

        return response_data


@authenticated
async def stream_api_request(
        method: HTTPMethod,
        url: str,
        *,
        params: list[tuple[str, str]] = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: APIRequestLoggingConfig = DEFAULT_LOGGING,
        _context: APIContext = None
) -> AsyncIterator[dict[str, Any]]:
    api_name = f"{log.api_name} stream API"
    error_msg = f"{api_name} request failed"
    extra_log: dict[str, Any] = {
        "API": api_name,
        "url": url,
        "server": _context.server,
        "method": method
    }
    _log.debug(f"Requesting {api_name}", extra=extra_log)

    response = await _raw_api_request(
        method=method,
        url=url,
        session=_context.session,
        data=None,
        params=params,
        headers=headers,
        processing=processing,
        log=log
    )

    async with response:
        if response.status == 204:
            # We should not get it with watch actually
            return

        if response.status >= 300:
            exc_cls = ERROR_TYPE_BY_CODE.get(response.status) or RESTAPIError

            # Load the whole response, nothing to stream
            response_data = await __load_aiohttp_response(response)
            is_json = type(response_data) is not str
            extra_log["status"] = response.status
            if log.should_log_response(response_data):
                extra_log["response"] = response_data
            if not is_json:
                _log.error(f"Received non-JSON response from {api_name}", extra=extra_log)
                response_data = {"data": response_data}

            api_error = exc_cls(response.status, error_msg, response_data)
            extra_log["error"] = str(api_error.status)
            if response.status not in log.not_error_statuses and exc_cls not in log.not_error_statuses:
                _log_level = _log.error
            elif log.on_success:
                _log_level = _log.info
            else:
                _log_level = _log.debug
            _log_level(error_msg, extra=extra_log)
            raise api_error

        while True:
            raw_line = await response.content.readline()
            if not raw_line:
                # EOF
                break

            line = raw_line.strip()
            if not line:
                continue

            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(f"Unable to decode k8s watch event JSON line: {e}. Offending line: {line!r}") from e


def __build_request_url(resource: Type[K8sResource] | K8sResource, name: str = None, namespace: str = None,
                        trim_name: bool = False) -> str:
    """
    `namespace` and `name` args have priority over the values in resource.metadata.
    """
    if isclass(resource):
        ns = namespace
        resource: Type[K8sResource]  # for the bugged pycharm typechecker
    else:
        ns = namespace or resource.metadata.namespace
        name = name or resource.metadata.name

    if resource.is_namespaced_:
        if ns:
            url = resource.api_path().format(namespace=ns)
        else:
            url = resource.api_path().replace("/namespaces/{namespace}", "")
    else:
        if ns:
            raise ValueError(f"Resource {resource.apiVersion} is cluster scoped, "
                             f"but namespace {ns} was specified in the metadata")
        url = resource.api_path()
    return f"{url.strip('/')}/{name}" if name and not trim_name else url


@overload
def __decode_k8s_rest_api_response(response: RESTAPIError) -> RESTAPIError[Status]: ...
@overload
def __decode_k8s_rest_api_response(response: list | dict) -> K8sResource: ...

def __decode_k8s_rest_api_response(response: list | dict | RESTAPIError):
    if isinstance(response, RESTAPIError):
        empty_status_err = "Empty Status object has been added to the response."
        if isinstance(response.response, dict):
            try:
                response.extra = Status.from_dict(response.response)
            except Exception as e:
                _log.critical(
                    f"K8s API error response does not match {Status.apiVersion} {Status.kind}. {empty_status_err} "
                    f"Check the version of your k8s models ASAP!",
                    extra={"error": str(e), "response": str(response.response)})
                response.extra = Status()
        else:
            _log.error(f"Got k8s API error with not a valid json. {empty_status_err}",
                          extra={"response": str(response.response)})
            response.extra = Status()
        return response
    api, kind = response.get("apiVersion"), response.get("kind")
    k8s_model = get_model(api, kind)
    if not k8s_model:
        raise TypeError(f"Unable to decode k8s API response by apiVersion and kind: {api, kind}. "
                        f"You must check your code or regenerate k8s models!")
    return k8s_model.from_dict(response)


ResourceT = TypeVar("ResourceT", bound=K8sResource)


#
# GET
#
@overload
async def get_k8s_resource(
        resource: Type[ResourceT],
        name: str,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT: ...

@overload
async def get_k8s_resource(
        resource: Type[ResourceT],
        name: str,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | RESTAPIError[Status]: ...

@overload
async def get_k8s_resource(
        resource: ResourceT,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT: ...

@overload
async def get_k8s_resource(
        resource: ResourceT,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | RESTAPIError[Status]: ...

@overload
async def get_k8s_resource(
        resource: Type[ResourceT],
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING
) -> K8sResourceList[ResourceT]: ...

async def get_k8s_resource(
        resource: Type[ResourceT] | ResourceT,
        name: str = None,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | RESTAPIError[Status]:
    method = HTTPMethod.GET
    try:
        response = await rest_api_request(
            method=method,
            url=f"{server.strip('/') if server else ''}/{__build_request_url(resource, name, namespace)}",
            params=params.to_http_params() if params else None,
            headers=headers,
            data=None,
            processing=processing,
            log=log,
            return_api_exceptions=return_api_exceptions
        )
        return __decode_k8s_rest_api_response(response)
    except Exception as e:
        if log.errors_as_critical or isinstance(e, TypeError):
            _log.critical(f"Error happened while attempting to {method} resource {resource.apiVersion}: {e}")
        if isinstance(e, RESTAPIError):
            raise __decode_k8s_rest_api_response(e)
        raise

#
# CREATE
#
@overload
async def create_k8s_resource(
        resource: ResourceT,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT | Status: ...

@overload
async def create_k8s_resource(
        resource: ResourceT,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]: ...

async def create_k8s_resource(
        resource: ResourceT,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]:
    method = HTTPMethod.POST
    bugged_api_backoff_limit, attempts = 3, 0
    while attempts < bugged_api_backoff_limit:
        attempts += 1
        try:
            response = await rest_api_request(
                method=method,
                url=f"{server.strip('/') if server else ''}/"
                    f"{__build_request_url(resource, namespace=namespace, trim_name=True)}",
                params=params.to_http_params() if params else None,
                headers=headers,
                data=resource.to_dict(),
                processing=processing,
                log=log,
                return_api_exceptions=return_api_exceptions
            )
            decoded_resp = __decode_k8s_rest_api_response(response)

            # Do endless attempts for 409 quota errors because of this bug
            #  https://github.com/kubernetes/kubernetes/issues/67761
            if isinstance(decoded_resp, RESTAPIError) and decoded_resp.status == 409:
                if attempts < bugged_api_backoff_limit and "the object has been modified" in decoded_resp.extra.message:
                    continue
            return decoded_resp

        except Exception as e:
            if log.errors_as_critical or isinstance(e, TypeError):
                _log.critical(f"Error happened while attempting to {method} resource {resource.apiVersion}: {e}")
            if isinstance(e, RESTAPIError):
                raise __decode_k8s_rest_api_response(e)
            raise

    raise  # for the bugged pycharm typechecker; will never happen


#
# UPDATE
#
def _normalize_pointer(ptr: str) -> str:
    return "/" + "/".join([s for s in (ptr or "").split("/") if s])


def _op_within_paths(op: dict[str, Any], allowed_ptrs: list[str]) -> bool:
    p = _normalize_pointer(op.get("path", ""))
    for q in allowed_ptrs:
        qn = _normalize_pointer(q)
        if p == qn or p.startswith(qn + "/"):
            return True
    return False


def _get_by_pointer(doc: Any, segments: list[str]) -> Any:
    current = doc
    for s in segments:
        if isinstance(current, list) and s.isdigit():
            i = int(s)
            if 0 <= i < len(current): current = current[i]
            else: return None
        elif isinstance(current, dict):
            if s in current:
                current = current[s]
            else:
                return None
        else:
            return None
    return current


def _find_merge_root_segments(new_doc: dict[str, Any], segments: list[str]) -> list[str]:
    """
    For merge-patch, arrays are forced as a whole.
    If path crosses a list index, promote merge root to the list key (path before the index).
    """
    current = new_doc
    for i, s in enumerate(segments):
        if isinstance(current, list):
            return segments[:i]
        if isinstance(current, dict):
            current = current.get(s)
        else:
            break
    return segments


def _set_by_segments(doc: dict[str, Any], segments: list[str], value: Any) -> None:
    current = doc
    for s in segments[:-1]:
        nxt = current.get(s)
        if not isinstance(nxt, dict):
            nxt = {}
            current[s] = nxt
        current = nxt
    if segments:
        current[segments[-1]] = value
    else:
        if isinstance(value, dict):
            doc.clear(); doc.update(value)
        else:
            raise TypeError("Root value for merge patch must be an object")


def _build_partial_spec_for_paths(resource: K8sResource, paths: list[PathPicker[Any]]) -> dict[str, Any]:
    """
    Build a partial merge-patch body that includes exactly the requested paths.
    If a path crosses a list element, the entire list at that key is set (list root promotion).
    """
    new_doc = resource.to_dict()
    partial: dict[str, Any] = {}
    for picker in paths:
        if hasattr(picker, "path"):
            segments = [str(s) for s in getattr(picker, "path")]
        else:
            segments = [s for s in picker.json_path_pointer().lstrip("/").split("/") if s]
        merge_root = _find_merge_root_segments(new_doc, segments)
        value = _get_by_pointer(new_doc, merge_root)
        _set_by_segments(partial, merge_root, value)
    return partial


@overload
async def update_k8s_resource(
        resource: ResourceT,
        name: str | None = None,
        namespace: str | None = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        built_from_latest: ResourceT = None,
        paths: list[PathPicker] = None,
        force: bool = False,
        ignore_list_conflicts: bool = False,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT | Status: ...

@overload
async def update_k8s_resource(
        resource: ResourceT,
        name: str | None = None,
        namespace: str | None = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        built_from_latest: ResourceT = None,
        paths: list[PathPicker] = None,
        force: bool = False,
        ignore_list_conflicts: bool = False,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]: ...

async def update_k8s_resource(
        resource: ResourceT,
        name: str | None = None,
        namespace: str | None = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        built_from_latest: ResourceT = None,
        paths: list[PathPicker] = None,
        force: bool = False,
        ignore_list_conflicts: bool = False,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]:

    # Do strategic merge if we can
    method = HTTPMethod.PATCH
    if PatchRequestType.strategic_merge in resource.patch_strategies_:
        content_type = PatchRequestType.strategic_merge
    else:
        content_type = PatchRequestType.merge

    try:
        headers = headers or {}
        name = name or getattr(resource.metadata, "name", None)
        namespace = namespace or getattr(resource.metadata, "namespace", None)
        url = f"{server.strip('/') if server else ''}/{__build_request_url(resource, name, namespace)}"

        # force overrides everything
        if force:
            method = HTTPMethod.PUT
            content_type = PatchRequestType.plain_json
            request_data = resource.to_dict()

        # If we have version to compare with, find the diff between them
        elif built_from_latest:
            old_dict, new_dict = built_from_latest.to_dict(), resource.to_dict()
            json_patch = json_patch_from_diff(old_dict, new_dict)

            # Exclude all paths which are not white-listed
            if paths:
                allowed_ptrs = [p.json_path_pointer() for p in paths]
                json_patch = [op for op in json_patch if "path" in op and _op_within_paths(op, allowed_ptrs)]

            # Do nothing if no white-listed diff was found
            if not json_patch:
                return built_from_latest

            # Build strategic merge if we can
            if content_type == PatchRequestType.strategic_merge:
                request_data = jsonpatch_to_smp(built_from_latest, json_patch)
            # Do jsonPatch otherwise
            else:
                content_type = PatchRequestType.json

                # Guard lists and list items from being forced via `test` directives
                if not ignore_list_conflicts:
                    json_patch = guard_lists_from_json_patch_replacement(json_patch, old_dict)
                request_data = json_patch

        # If we know paths to merge, pick them all
        elif paths:
            request_data = _build_partial_spec_for_paths(resource, paths)

        # Give up, let k8s API merge as is
        else:
            request_data = resource.to_dict()

        if not request_data:
            _log.warning(
                f"Got empty request_data after patch evaluation. Update hasn't been sent to cluster.",
                extra={"paths": paths, "force": force, "latest_version_passed": bool(built_from_latest)})
            return resource

        # ToDo: Add more intelligent conflict resolve here. We could know that one of the jsonPatch `test` guards
        #  caused a conflict, so we could add some SQL-style arg `on_conflict`, where user can define
        #  that he wants skip certain paths from update on conflict or force them (in this case we could not add
        #  a test guard at all).
        #  In case of skipping, the function would remove conflicted path from the patch and retry an update again.
        response = await rest_api_request(
            method=method,
            url=url,
            params=params.to_http_params() if params else None,
            headers=headers | {"Content-Type": content_type.value},
            data=request_data,
            processing=processing,
            log=log,
            return_api_exceptions=return_api_exceptions
        )
        return __decode_k8s_rest_api_response(response)

    except Exception as e:
        if log.errors_as_critical or isinstance(e, TypeError):
            _log.critical(f"Error happened while attempting to {method} resource {resource.apiVersion}: {e}")
        if isinstance(e, RESTAPIError):
            raise __decode_k8s_rest_api_response(e)
        raise


#
# DELETE
#
@overload
async def delete_k8s_resource(
        resource: Type[ResourceT],
        name: str = None,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        delete_options: DeleteOptions = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT | Status: ...

@overload
async def delete_k8s_resource(
        resource: ResourceT,
        name: str = None,
        namespace: str = None,
        *
        server: str,
        headers: dict[str, str] = None,
        delete_options: DeleteOptions = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Literal[None] = None
) -> ResourceT | Status: ...

@overload
async def delete_k8s_resource(
        resource: Type[ResourceT],
        name: str = None,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        delete_options: DeleteOptions = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]: ...

@overload
async def delete_k8s_resource(
        resource: ResourceT,
        name: str = None,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        delete_options: DeleteOptions = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]: ...

async def delete_k8s_resource(
        resource: Type[ResourceT] | ResourceT,
        name: str = None,
        namespace: str = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        delete_options: DeleteOptions = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
        return_api_exceptions: Sequence[int | Type[RESTAPIError]] = None
) -> ResourceT | Status | RESTAPIError[Status]:
    method = HTTPMethod.DELETE
    try:
        response = await rest_api_request(
            method=method,
            url=f"{server.strip('/') if server else ''}/{__build_request_url(resource, name=name, namespace=namespace)}",
            params=params.to_http_params() if params else None,
            headers=headers,
            data=delete_options.to_dict() if delete_options else None,
            processing=processing,
            log=log,
            return_api_exceptions=return_api_exceptions
        )
        return __decode_k8s_rest_api_response(response)
    except Exception as e:
        if log.errors_as_critical or isinstance(e, TypeError):
            _log.critical(f"Error happened while attempting to {method} resource {resource.apiVersion}: {e}")
        if isinstance(e, RESTAPIError):
            raise __decode_k8s_rest_api_response(e)
        raise


async def create_or_update_k8s_resource(
        resource: ResourceT,
        name: str | None = None,
        namespace: str | None = None,
        *,
        server: str = None,
        params: K8sQueryParams = None,
        headers: dict[str, str] = None,
        paths: list[PathPicker] = None,
        force: bool = False,
        ignore_list_conflicts: bool = False,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING
) -> ResourceT | Status:

    # We catch 403 here too, because if there's ResourceQuota set, and it's drained,
    # it will return 403 instead of 409 even if resource with this name exists.
    # ToDo: Catch ResourceQuota Status text specifically to avoid retrying with no permissions
    create_log = replace(log, not_error_statuses=[403, 409])
    update_log = replace(log, not_error_statuses=[404])
    attempts, max_attempts = 0, 3
    while attempts < max_attempts:
        attempts += 1
        try:
            response = await create_k8s_resource(
                resource, namespace,
                server=server,
                params=params,
                headers=headers,
                log=create_log)
            return response
        except ConflictError or ForbiddenError as e :
            try:
                response = await update_k8s_resource(
                    resource, name, namespace,
                    server=server,
                    paths=paths,
                    force=force,
                    ignore_list_conflicts=ignore_list_conflicts,
                    params=params,
                    headers=headers,
                    log=update_log)
                return response
            except NotFoundError:
                if attempts >= max_attempts:
                    raise e
                await asyncio.sleep(2)

    raise  # for the bugged pycharm typechecker; will never happen


#
# WATCH
#
class WatchEventType(StrEnum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    BOOKMARK = "BOOKMARK"
    ERROR = "ERROR"


@dataclass(slots=True, frozen=True, kw_only=True)
class K8sResourceEvent(Loadable, Generic[ResourceT]):
    type: WatchEventType
    object: ResourceT | Status


def __decode_k8s_watch_event(event: dict):
    return K8sResourceEvent(
        type=event.get("type"), object=__decode_k8s_rest_api_response(dict(event.get("object") or {})))


async def watch_k8s_resources(
        resource: Type[ResourceT] | ResourceT,
        name: str | None = None,
        namespace: str | None = None,
        *,
        server: str | None = None,
        params: K8sQueryParams | None = None,
        headers: dict[str, str] | None = None,
        processing: APIRequestProcessingConfig = DEFAULT_PROCESSING,
        log: K8sAPIRequestLoggingConfig = DEFAULT_LOGGING,
) -> AsyncIterator[K8sResourceEvent[ResourceT]]:
    """
    Example:
        async for event in watch_k8s_resources(Pod, namespace="default"):
            print(event.type, event.object.metadata.name)
    """
    method = HTTPMethod.GET
    params = params or K8sQueryParams()
    try:
        stream = stream_api_request(
            method=method,
            url=f"{server.strip('/') if server else ''}/{__build_request_url(resource, name, namespace)}",
            params=replace(params, watch=True).to_http_params(),
            headers=headers,
            processing=processing,
            log=log
        )
        async for event in stream:
            yield __decode_k8s_watch_event(event)
    except Exception as e:
        if log.errors_as_critical or isinstance(e, TypeError):
            _log.critical(f"Error happened while attempting to {method} resource {resource.apiVersion}: {e}")
        if isinstance(e, RESTAPIError):
            raise __decode_k8s_rest_api_response(e)
        raise
