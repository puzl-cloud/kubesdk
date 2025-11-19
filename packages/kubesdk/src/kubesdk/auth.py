# SPDX-License-Identifier: MIT
# Portions of this file are derived from the Kopf project:
#   https://github.com/nolar/kopf
# Copyright (c) 2020 Sergey Vasilyev <nolar@nolar.info>
# Copyright (c) 2019-2020 Zalando SE
# Licensed under the MIT License; see the LICENSE file or https://opensource.org/licenses/MIT
from __future__ import annotations

import asyncio
import string
import secrets
import base64
import functools
import os
import ssl
import threading
import tempfile
from contextvars import ContextVar
from typing import Any, Callable, Generic, Iterator, Mapping, TypeVar, cast, Awaitable

import aiohttp

from .common import host_from_url
from .errors import *
from .credentials import Vault, ConnectionInfo, LoginError


T = TypeVar("T")
DEFAULT_VAULT_NAME = "default"


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
        self._global_value: T = None

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
_auth_vault_var: GlobalContextVar[dict[str, Vault]] = GlobalContextVar("_auth_vault_var")


_F = TypeVar("_F", bound=Callable[..., Any])


def authenticated(fn: _F) -> _F:
    """
    A decorator to inject a pre-authenticated session to a requesting routine.
    If the wrapped function fails with UnauthorizedError, the vault is asked to re-login
    and the function is retried with a new context until success or a fatal error occurs.
    """
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # We have this undocumented in a rare case of multiple clients with different RBAC within one cluster.
        # Should never be used normally.
        session_key = kwargs.pop("_session_key", "default")
        explicit_context: APIContext | None = kwargs.pop("_context", None)
        if explicit_context is not None:
            return await explicit_context.call(fn, *args, **kwargs)

        vault_key = host_from_url(kwargs.get("url")) or DEFAULT_VAULT_NAME
        vaults = _auth_vault_var.get()
        vault = vaults.get(vault_key)
        forbidden_err = None
        async for key, info, context in vault.extended(APIContext, f"context-{session_key}"):
            try:
                return await context.call(fn, *args, **kwargs)
            except UnauthorizedError as e:
                await vault.invalidate(key, info, exc=e)
            except ForbiddenError as e:
                # NB: We do not invalidate credentials on 403 because we might have separate contexts
                # with different accounts for different Roles. One might access one resource,
                # and have no access to another resource within the same cluster.
                # However, using multiple accounts in the same process within the same cluster is NOT a good practice.
                # Such a setup can lead to invalid credentials renewing due to wait_for_emptiness() mechanics.
                forbidden_err = e
            except RuntimeError as e:
                if not context.closed:
                    raise
                await vault.invalidate(key, info, exc=e)

        raise forbidden_err or UnauthorizedError()

    return cast(_F, wrapper)


class _Worker:
    """
    A worker owns an asyncio event loop and a list of sessions created on that loop.
    """
    def __init__(self, worker_index: int, sessions_per_worker: int, session_factory: Callable[[], Any]):
        self.worker_index = worker_index
        self.sessions_per_worker = max(1, int(sessions_per_worker))
        self._session_factory = session_factory

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run,
            name=f"api-worker-{worker_index}",
            daemon=True
        )
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._sessions: list[Any] = []

        self._thread.start()
        self._ready.wait()

    @property
    def loop(self) -> asyncio.AbstractEventLoop: return self._loop
    @property
    def sessions(self) -> list[Any]: return self._sessions

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def _init() -> None:
            for _ in range(self.sessions_per_worker):
                session = self._session_factory()
                if asyncio.iscoroutine(session):
                    session = await session
                self._sessions.append(session)
            self._ready.set()

        self._loop.run_until_complete(_init())
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._close_sessions())
            self._loop.close()

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

    def run_coroutine(self, coro: Awaitable[Any]):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if not self._closing.is_set():
            self._closing.set()
            def _stop(loop): loop.stop()
            self._loop.call_soon_threadsafe(_stop, self._loop)
            self._thread.join(timeout=5)


class APIContext:
    """
    Multi-thread, multi-session context with full TLS/auth header logic.

    - Owns `threads` worker threads, each with its own event loop.
    - Each worker has `pool_size` sessions created on its loop via session_factory.
    - session_factory is either provided or built from TLS/auth info.
    - .call(fn, ...) picks a (worker, session) via round-robin, runs fn on that worker loop,
        and binds .session/.loop through a ContextVar.
    """

    server: str
    default_namespace: str | None

    def __init__(self, info: ConnectionInfo, pool_size: int = 4, threads: int = 1,
                 session_factory: Callable[[], Any] = None) -> None:
        self.server = info.server_info.server
        self.default_namespace = info.default_namespace

        rand_string = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        tempfiles = _TempFiles(f"_{rand_string}")

        ca_path = None
        client_cert_path = None
        client_key_path = None

        ca_path_cfg = info.server_info.certificate_authority
        ca_data_cfg = info.server_info.certificate_authority_data
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

        if info.server_info.insecure_skip_tls_verify:
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
                timeout=aiohttp.ClientTimeout(total=60),
                base_url=self.server,
                headers=headers,
                auth=auth
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

        # Per-task binding of (worker_idx, session_idx) for .session / .loop / .call
        self._current_addr: ContextVar[tuple[int, int]] = ContextVar(f"api_ctx_addr_{id(self)}")


    def _choose_address(self) -> tuple[int, int]:
        with self._rr_lock:
            idx = self._rr_counter % len(self._address_book)
            self._rr_counter += 1
            return self._address_book[idx]

    @property
    def session(self) -> Any:
        """
        Real aiohttp.ClientSession bound to this context call.

        Must only be used from within a coroutine that is currently executing inside
        APIContext.call(). Using it outside that will raise RuntimeError.
        """
        try:
            worker_idx, session_idx = self._current_addr.get()
        except LookupError:
            raise RuntimeError("APIContext.session used outside APIContext.call()")
        # Access the concrete session object on that worker
        return self._workers[worker_idx]._sessions[session_idx]

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """
        Event loop of the worker currently bound to this context call.
        """
        try:
            worker_idx, session_idx = self._current_addr.get()
        except LookupError:
            raise RuntimeError("APIContext.loop used outside APIContext.call()")
        return self._workers[worker_idx].loop

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """
        Run user async function `fn` on one worker's loop with one specific session bound.

        Inside `fn`, `_context` will be `self`, and `_context.session` / `_context.loop`
        refer to the chosen worker+session.

        This is what @authenticated should use to execute `fn` safely.
        """
        if self._closed.is_set():
            raise RuntimeError("APIContext is closed")

        worker_idx, session_idx = self._choose_address()
        worker = self._workers[worker_idx]

        async def _runner() -> Any:
            token = self._current_addr.set((worker_idx, session_idx))
            try:
                return await fn(*args, **kwargs, _context=self)
            finally:
                self._current_addr.reset(token)

        fut = asyncio.run_coroutine_threadsafe(_runner(), worker.loop)
        return await asyncio.wrap_future(fut)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for w in self._workers:
            w.stop()
        # tempfiles will be purged by _TempFiles.__del__


class _TempFiles(Mapping[bytes, str]):
    """
    A container for the temporary files, which are purged on garbage collection.

    The files are purged when the container is garbage-collected. The container
    is garbage-collected when its parent `APISession` is garbage-collected or
    explicitly closed (by `Vault` on removal of corresponding credentials).
    """
    _path_suffix: str
    _paths: dict[bytes, str]

    def __init__(self, path_suffix: str) -> None:
        super().__init__()
        self._paths: dict[bytes, str] = {}
        self._path_suffix = path_suffix

    def __del__(self) -> None:
        self.purge()

    def __len__(self) -> int:
        return len(self._paths)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._paths)

    def __getitem__(self, item: bytes) -> str:
        if item not in self._paths:
            with tempfile.NamedTemporaryFile(delete=False, suffix=self._path_suffix) as f:
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


_auth_vault_var.set({})
