from __future__ import annotations

import asyncio
import inspect
import json
import string
import secrets
import base64
import functools
import os
import ssl
import threading
from contextvars import ContextVar
from typing import Any, Callable, Generic, TypeVar, cast, Awaitable, AsyncIterable

import aiohttp

from .common import host_from_url
from .errors import *
from .credentials import Vault, ConnectionInfo, LoginError
from ._temp_files import _TempFiles


T = TypeVar("T")
DEFAULT_VAULT_NAME = "default"
POOL_SIZE = int(os.getenv("KUBESDK_CLIENT_POOL_SIZE", 2))
THREADS = int(os.getenv("KUBESDK_CLIENT_THREADS", 2))
MAX_STREAMS_PER_LOOP = int(os.getenv("KUBESDK_MAX_STREAMS_PER_LOOP", 25))


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
    async def gen_wrapper(*args: Any, **kwargs: Any):
        # We have this undocumented in a rare case of multiple clients with different RBAC within one cluster.
        # Should never be used normally.
        session_key = kwargs.pop("_session_key", "default")
        explicit_context: APIContext | None = kwargs.pop("_context", None)

        async def _run_with_context(ctx: APIContext):
            """
            Call `fn` through the context, handling both:
            - context.call(...) being awaitable and returning an async generator
            - context.call(...) directly returning an async generator
            """
            results = await ctx.call(fn, *args, **kwargs)
            async for res in results:
                yield res

        if explicit_context is not None:
            async for item in _run_with_context(explicit_context):
                yield item
            return

        vault_key = host_from_url(kwargs.get("url")) or DEFAULT_VAULT_NAME
        vaults = _auth_vault_var.get()
        vault = vaults.get(vault_key)
        if vault is None:
            raise UnauthorizedError()

        forbidden_err: ForbiddenError | None = None
        async for key, info, context in vault.extended(APIContext, f"context-{session_key}"):
            try:
                async for item in _run_with_context(context):
                    yield item
                return
            except UnauthorizedError as e:
                await vault.invalidate(key, info, exc=e)
            except ForbiddenError as e:
                forbidden_err = e
            except RuntimeError as e:
                if not context.closed:
                    raise
                await vault.invalidate(key, info, exc=e)

        raise forbidden_err or UnauthorizedError()


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
                # If context is already closed, invalidate. Otherwise, bubble up.
                if not context.closed:
                    raise
                await vault.invalidate(key, info, exc=e)

        raise forbidden_err or UnauthorizedError()

    is_generator = inspect.isasyncgenfunction(fn)
    return cast(_F, gen_wrapper) if is_generator else cast(_F, wrapper)


class _Worker:
    """
    A worker owns an asyncio event loop and a list of sessions created on that loop.
    """
    def __init__(self, worker_index: int, sessions_per_worker: int, session_factory: Callable):
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
                except BaseException:
                    pass
            elif callable(close):
                try:
                    close()
                except BaseException:
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

    threads: int
    pool_size: int
    server: str
    default_namespace: str | None

    def __init__(self, info: ConnectionInfo, pool_size: int = POOL_SIZE, threads: int = THREADS,
                 session_factory: Callable = None) -> None:
        self.server = info.server_info.server
        self.default_namespace = info.default_namespace
        self.pool_size = pool_size
        self.threads = threads

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

        def default_factory(stream: bool = False):
            return aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=MAX_STREAMS_PER_LOOP if stream else 100,
                    ssl=ssl_context,
                    keepalive_timeout=120 if not stream else None
                ),
                timeout=aiohttp.ClientTimeout(total=60),
                read_bufsize=2 ** 21,  # 2 MB (4MB effective limit). Enough for the default k8s object limit of 1MB.
                max_line_size=2 ** 20,
                json_serialize=functools.partial(json.dumps, separators=(',', ':')),
                base_url=self.server,
                headers=headers,
                auth=auth
            )

        self._session_factory: Callable = session_factory or default_factory

        self._sessions_per_worker = max(1, int(pool_size))
        self._num_workers = max(1, int(threads))

        self._workers: list[_Worker] = []
        for worker_index in range(self._num_workers):
            self._workers.append(_Worker(worker_index, self._sessions_per_worker, self._session_factory))

        # Build a flat address list of all sessions
        self._address_book: list[tuple[int, int]] = []
        for worker in range(self._num_workers):
            for session in range(self._sessions_per_worker):
                self._address_book.append((worker, session))

        self._rr_lock = threading.Lock()
        self._rr_counter = 0
        self._closed = threading.Event()

        # Keep tempfiles for manual cleanup if needed
        self._tempfiles = tempfiles

        # Per-task binding of (worker_idx, session_idx) for .session / .loop / .call
        self._current_addr: ContextVar[tuple[int, int]] = ContextVar(f"api_ctx_addr_{id(self)}")

        # Stream-watch clients, one per event loop
        self._stream_clients: dict[int, aiohttp.ClientSession] = {}

    def _choose_address(self) -> tuple[int, int]:
        with self._rr_lock:
            size = len(self._address_book)
            if size == 0:
                raise RuntimeError("APIContext session address book is empty")

            # Normalized round-robin counter
            idx = self._rr_counter % size
            self._rr_counter = (idx + 1) % size
            return self._address_book[idx]

    @property
    def session(self) -> aiohttp.ClientSession:
        """
        Real aiohttp.ClientSession bound to this context call.
        For normal calls: returns worker/session-bound client.
        For generator calls: returns client created in the caller's loop.
        """
        try:
            worker_idx, session_idx = self._current_addr.get()
        except LookupError:
            # Generator path: use per-loop client
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                raise RuntimeError("APIContext.session used outside APIContext.call()")

            try:
                return self._stream_clients[id(loop)]
            except KeyError:
                raise RuntimeError("APIContext.session used outside APIContext.call()")

        # Normal worker/session path
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

    async def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Awaitable | AsyncIterable:
        """
        Run user async function `fn` on one worker's loop with one specific session bound.

        Inside `fn`, `_context` will be `self`, and `_context.session` / `_context.loop`
        refer to the chosen worker+session for non-watch queries.

        This is what any consumer (e.g. @authenticated) should use to execute `fn` safely.
        """
        if self.closed:
            raise RuntimeError("APIContext is closed")

        worker_idx, session_idx = self._choose_address()
        worker = self._workers[worker_idx]

        is_generator = inspect.isasyncgenfunction(fn)
        if not is_generator:
            async def _runner() -> Any:
                token = self._current_addr.set((worker_idx, session_idx))
                try:
                    return await fn(*args, **kwargs, _context=self)
                finally:
                    self._current_addr.reset(token)

            fut = asyncio.run_coroutine_threadsafe(_runner(), worker.loop)
            return await asyncio.wrap_future(fut)

        # Get the loop where `await call()` is happening
        loop = asyncio.get_running_loop()
        loop_id = id(loop)

        # Create or reuse httpx.AsyncClient bound to this loop
        if loop_id not in self._stream_clients:
            self._stream_clients[loop_id] = self._session_factory()

        async def _proxy():
            """
            Async generator that the caller sees.

            Runs on the caller's loop, uses per-loop client via `session` property.
            No cross-thread communication, no ContextVar juggling.
            """
            agen = fn(*args, **kwargs, _context=self)
            async for item in agen:
                yield item

        return _proxy()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        if self.closed:
            return
        self._closed.set()
        for w in self._workers:
            w.stop()
        # tempfiles will be purged by _TempFiles.__del__


_auth_vault_var.set(dict())
