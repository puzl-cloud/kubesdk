# SPDX-License-Identifier: MIT
# Portions of this file are derived from the Kopf project:
#   https://github.com/nolar/kopf
# Copyright (c) 2020 Sergey Vasilyev <nolar@nolar.info>
# Copyright (c) 2019-2020 Zalando SE
# Licensed under the MIT License; see the LICENSE file or https://opensource.org/licenses/MIT
from __future__ import annotations

import asyncio
import base64
import functools
import os
import ssl
import threading
import tempfile
from contextvars import ContextVar
from typing import Any, Callable, Generic, Iterator, Mapping, Optional, TypeVar, cast

import aiohttp

from .common import host_from_url
from .errors import *
from .credentials import Vault, ConnectionInfo, LoginError


T = TypeVar("T")


class GlobalContextVar(Generic[T]):
    """
    A ContextVar wrapper with a process-wide default.
    Setting the value updates both the local context and the global default.
    Getting the value returns the local context value when present,
    otherwise it returns the last process-wide value that was set.
    """

    __slots__ = ("_local", "_has_global", "_global_value")

    def __init__(self, name: str):
        self._local: ContextVar[T] = ContextVar(name + "_local")
        self._has_global: bool = False
        self._global_value: Optional[T] = None

    def set(self, value: T):
        token = self._local.set(value)
        self._has_global = True
        self._global_value = value
        return token

    def get(self) -> T:
        try:
            return self._local.get()
        except LookupError:
            if self._has_global:
                # Safe to cast because we only set via .set
                return cast(T, self._global_value)
            raise

    def reset(self, token) -> None:
        self._local.reset(token)


# Per-controller storage and exchange point for authentication methods.
# This uses GlobalContextVar to behave the same way across threads and event loops.
auth_vault_var: GlobalContextVar[Vault] = GlobalContextVar("auth_vault_var")


_F = TypeVar("_F", bound=Callable[..., Any])


def authenticated(fn: _F) -> _F:
    """
    A decorator to inject a pre-authenticated session to a requesting routine.
    If the wrapped function fails with UnauthorizedError, the vault is asked to re-login
    and the function is retried with a new context until success or a fatal error occurs.
    """
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "context" in kwargs:
            return await fn(*args, **kwargs)

        if "session_context_key" in kwargs:
            session_context_key = kwargs["session_context_key"]
        else:
            session_context_key = host_from_url(kwargs.get("url")) or "UNKNOWN-SERVER"

        vault: Vault = auth_vault_var.get()
        async for key, info, context in vault.extended(APIContext, f"context-{session_context_key}"):
            try:
                return await fn(*args, **kwargs, context=context)
            except UnauthorizedError as e:
                await vault.invalidate(key, info, exc=e)
            except RuntimeError as e:
                if not context.closed:
                    raise
                await vault.invalidate(key, info, exc=e)

        raise RuntimeError("Reached an impossible state: the end of the authentication cycle.")

    return cast(_F, wrapper)


class AiohttpSessionProxy:
    """
    A lightweight proxy that exposes aiohttp-like methods. We need this because aiohttp sessions are not thread-safe.
    It forwards requests into the worker pool and returns the worker result.
    """
    def __init__(self, context: APIContext):
        self._context = context

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        return await self._context.request(method, url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> Any:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> Any:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> Any:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> Any:
        return await self.request("OPTIONS", url, **kwargs)


class _Worker:
    """
    A worker owns an asyncio event loop and a list of sessions created on that loop.
    """
    def __init__(self, worker_index: int, sessions_per_worker: int, session_factory: Callable[[], Any]):
        self.worker_index = worker_index
        self.sessions_per_worker = max(1, int(sessions_per_worker))
        self._session_factory = session_factory

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=f"api-worker-{worker_index}", daemon=True)
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._sessions: list[Any] = []

        self._thread.start()
        self._ready.wait()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._create_sessions())
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            # Best effort close of sessions
            self._loop.run_until_complete(self._close_sessions())
            self._loop.stop()
            self._loop.close()

    async def _create_sessions(self) -> None:
        # Sessions must be created in the worker loop
        for _ in range(self.sessions_per_worker):
            session = self._session_factory()
            # Support async factory returning a session
            if asyncio.iscoroutine(session):
                session = await session
            self._sessions.append(session)

    async def _close_sessions(self) -> None:
        for s in self._sessions:
            close = getattr(s, "close", None)
            if asyncio.iscoroutinefunction(close):
                try:
                    await close()
                except Exception:
                    pass
            elif callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._sessions.clear()

    def submit_request(self, session_index: int, method: str, url: str, **kwargs: Any):
        async def _do():
            session = self._sessions[session_index]
            req = getattr(session, "request", None)
            if req is None:
                raise RuntimeError("Session does not have request()")
            result = req(method, url, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

        return asyncio.run_coroutine_threadsafe(_do(), self._loop)

    def stop(self) -> None:
        if not self._closing.is_set():
            self._closing.set()
            def _stop(loop): loop.stop()
            self._loop.call_soon_threadsafe(_stop, self._loop)
            self._thread.join(timeout=5)


class APIContext:
    """
    A container around a pool of worker threads. Each worker owns an aiohttp session pool.
    Requests are routed to sessions using a global round-robin across all sessions,
    regardless of the caller thread or event loop.
    """
    server: str
    default_namespace: Optional[str]

    def __init__(self, info: ConnectionInfo, pool_size: int = 4, threads: int = 1,
                 session_factory: Optional[Callable[[], Any]] = None) -> None:
        self.server = info.cluster_info.server
        self.default_namespace = info.default_namespace

        tempfiles = _TempFiles()

        ca_path: Optional[str] = None
        client_cert_path: Optional[str] = None
        client_key_path: Optional[str] = None

        ca_path_cfg = info.cluster_info.certificate_authority
        ca_data_cfg = info.cluster_info.certificate_authority_data
        if ca_path_cfg and ca_data_cfg:
            raise LoginError("Both CA path and data are set. Need only one.")
        elif ca_path_cfg:
            ca_path = ca_path_cfg
        elif ca_data_cfg:
            ca_path = tempfiles[base64.b64decode(ca_data_cfg)]

        client_cert_path_cfg = info.client_info.client_certificate
        client_cert_data_cfg = info.client_info.client_certificate_data
        client_key_path_cfg = info.client_info.client_key
        client_key_data_cfg = info.client_info.client_key_data

        if client_cert_path_cfg and client_cert_data_cfg:
            raise LoginError("Both client certificate path and data are set. Need only one.")
        elif client_cert_path_cfg:
            client_cert_path = client_cert_path_cfg
        elif client_cert_data_cfg:
            client_cert_path = tempfiles[base64.b64decode(client_cert_data_cfg)]

        if client_key_path_cfg and client_key_data_cfg:
            raise LoginError("Both client private key path and data are set. Need only one.")
        elif client_key_path_cfg:
            client_key_path = client_key_path_cfg
        elif client_key_data_cfg:
            client_key_path = tempfiles[base64.b64decode(client_key_data_cfg)]

        # Build SSL context
        if client_cert_path and client_key_path:
            ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=ca_path)
            ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_key_path)
        else:
            ssl_context = ssl.create_default_context(cafile=ca_path)

        if info.cluster_info.insecure_skip_tls_verify:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        headers: dict[str, str] = {}
        scheme, token = info.client_info.scheme, info.client_info.token
        username, password = info.client_info.username, info.client_info.password
        if scheme and token:
            headers["Authorization"] = f"{scheme} {token}"
        elif scheme:
            headers["Authorization"] = f"{scheme}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        headers["User-Agent"] = "puzl.cloud/kubesdk"

        # auth for aiohttp only when both present
        auth = None
        if username and password:
            # Delay import until runtime for environments without aiohttp
            auth = aiohttp.BasicAuth(username, password)

        def default_factory():
            return aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=0, ssl=ssl_context),
                headers=headers,
                auth=auth,
            )

        self._session_factory: Callable[[], Any] = session_factory or default_factory

        self._sessions_per_worker = max(1, int(pool_size))
        self._num_workers = max(1, int(threads))

        self._workers: list[_Worker] = []
        for worker_index in range(self._num_workers):
            self._workers.append(_Worker(worker_index, self._sessions_per_worker, self._session_factory))

        # Build a flat address list of all sessions
        self._address_book: list[tuple[int, int]] = []
        for w in range(self._num_workers):
            for s in range(self._sessions_per_worker):
                self._address_book.append((w, s))

        self._rr_lock = threading.Lock()
        self._rr_counter = 0
        self._closed = threading.Event()

        # Keep tempfiles for manual cleanup if needed
        self._tempfiles = tempfiles

    def _choose_address(self) -> tuple[int, int]:
        with self._rr_lock:
            idx = self._rr_counter % len(self._address_book)
            self._rr_counter += 1
            return self._address_book[idx]

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._closed.is_set():
            raise RuntimeError("APIContext is closed")
        worker_idx, session_idx = self._choose_address()
        future = self._workers[worker_idx].submit_request(session_idx, method, url, **kwargs)
        return await asyncio.wrap_future(future)

    @property
    def session(self) -> AiohttpSessionProxy:
        """
        Return a proxy that forwards aiohttp-like requests into the worker pool.
        The proxy is safe to use from any thread or event loop.
        """
        return AiohttpSessionProxy(self)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for w in self._workers:
            w.stop()


class _TempFiles(Mapping[bytes, str]):
    """
    A container for the temporary files, which are purged on garbage collection.

    The files are purged when the container is garbage-collected. The container
    is garbage-collected when its parent `APISession` is garbage-collected or
    explicitly closed (by `Vault` on removal of corresponding credentials).
    """

    def __init__(self) -> None:
        super().__init__()
        self._paths: dict[bytes, str] = {}

    def __del__(self) -> None:
        self.purge()

    def __len__(self) -> int:
        return len(self._paths)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._paths)

    def __getitem__(self, item: bytes) -> str:
        if item not in self._paths:
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(item)
            self._paths[item] = f.name
        return self._paths[item]

    def purge(self) -> None:
        for _, path in self._paths.items():
            try:
                os.remove(path)
            except OSError:
                pass  # already removed
        self._paths.clear()
