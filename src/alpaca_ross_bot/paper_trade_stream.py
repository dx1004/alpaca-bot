"""Bounded paper trade-update transport; the caller owns evidence and recovery.

Private SDK hooks are deliberately limited to alpaca-py 0.44.0's connect,
authentication and subscription primitives. The SDK's logging/reconnect loop is
never started. No stream session establishes order or position reconciliation.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from math import isfinite
from typing import Protocol, TypeVar, cast
from uuid import uuid4

from alpaca.trading.stream import TradingStream

from alpaca_ross_bot.config import (
    PAPER_TRADING_URL,
    ConfigError,
    PaperConfig,
    PaperCredentials,
)

_PAPER_STREAM_URL = "wss://paper-api.alpaca.markets/stream"
_T = TypeVar("_T")


class PaperTradeStreamError(ValueError):
    """A local stream contract or evidence callback failed, without payloads."""


class _EvidenceFailure(PaperTradeStreamError):
    pass


class _Stopped(Exception):
    pass


class _DurationElapsed(Exception):
    pass


class _Socket(Protocol):
    async def recv(self) -> str | bytes: ...

    async def send(self, data: str) -> None: ...

    async def close(self) -> None: ...


class _Sdk(Protocol):
    _endpoint: str
    _raw_data: bool
    _ws: _Socket | None

    def subscribe_trade_updates(
        self, handler: Callable[[object], Awaitable[None]]
    ) -> None: ...

    async def _connect(self) -> None: ...

    async def _auth(self) -> None: ...

    async def _subscribe_trade_updates(self) -> None: ...

    async def close(self) -> None: ...


type SdkFactory = Callable[..., _Sdk]


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PaperTradeStreamError("stream JSON has duplicate fields")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise PaperTradeStreamError("stream JSON has a nonfinite number")


async def _unused_handler(message: object) -> None:
    """Register SDK subscription intent; SDK dispatch is deliberately unused."""


class _CapturedSocket:
    """Preserve frames before the SDK's auth decoder or local protocol checks."""

    def __init__(
        self, socket: _Socket, owner: "PaperTradeUpdateStream", session: str
    ) -> None:
        self.socket = socket
        self.owner = owner
        self.session = session
        self.authenticating = True
        self.message: dict[str, object] = {}

    async def recv(self) -> str | bytes:
        raw = await self.socket.recv()
        if not isinstance(raw, (bytes, str)) or len(raw) > self.owner._max_frame_bytes:
            raise PaperTradeStreamError("stream frame exceeds its contract")
        encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(encoded) > self.owner._max_frame_bytes:
            raise PaperTradeStreamError("stream frame exceeds its byte cap")
        self.owner._frame(encoded, self.session)
        try:
            message = json.loads(
                encoded,
                parse_float=Decimal,
                parse_constant=_reject_constant,
                object_pairs_hook=_json_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            raise PaperTradeStreamError("stream JSON is invalid") from None
        if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
            raise PaperTradeStreamError("stream message shape is invalid")
        self.message = message
        if self.authenticating and (
            message.get("stream") != "authorization"
            or message["data"].get("status") != "authorized"
        ):
            raise PaperTradeStreamError("stream authentication was not confirmed")
        return raw

    async def send(self, data: str) -> None:
        await self.socket.send(data)

    async def close(self) -> None:
        await self.socket.close()


class PaperTradeUpdateStream:
    """Finite, paper-only sessions with explicit subscription confirmation.

    Every bounded incoming frame, including control or invalid JSON frames, is
    delivered to ``on_frame`` before decoding. Text frames are encoded as UTF-8;
    binary bytes remain unchanged. The caller must treat frames before a
    session's SUBSCRIBED health record as untrusted and keep every disconnect as
    a recovery gap. There is no replay claim or exposure authority here.

    Callbacks run synchronously and must themselves be bounded. Their failures
    stop the run and propagate a sanitized PaperTradeStreamError without retry.
    ``verify_account`` must use its own bounded, paper-only read operation.
    """

    def __init__(
        self,
        config: PaperConfig,
        credentials: PaperCredentials,
        account_ref: str,
        *,
        verify_account: Callable[[], str],
        on_frame: Callable[[bytes, datetime, str], None],
        on_health: Callable[[str, datetime, str], None],
        timeout_seconds: float,
        max_frame_bytes: int,
        sdk_factory: SdkFactory | None = None,
    ) -> None:
        if config.trading_base_url != PAPER_TRADING_URL:
            raise ConfigError("trade stream requires the exact paper endpoint")
        if not isinstance(account_ref, str) or not account_ref.strip():
            raise ValueError("paper account reference must be nonempty")
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("stream timeout must be positive and finite")
        if type(max_frame_bytes) is not int or max_frame_bytes < 1:
            raise ValueError("stream frame cap must be positive")
        if sdk_factory is None and version("alpaca-py") != "0.44.0":
            raise PaperTradeStreamError("trade stream requires alpaca-py 0.44.0")
        self._credentials = credentials
        self._account_ref = account_ref
        self._verify_account = verify_account
        self._on_frame = on_frame
        self._on_health = on_health
        self._timeout = timeout_seconds
        self._max_frame_bytes = max_frame_bytes
        self._sdk_factory = sdk_factory or cast(SdkFactory, TradingStream)
        self._running = False
        self._stop_requested = False
        self._stop_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback_failed = False

    def stop(self) -> None:
        """Interrupt current work or prevent a later run; never invoke SDK.stop."""
        self._stop_requested = True
        if self._stop_event is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _health(self, state: str, session: str) -> None:
        try:
            self._on_health(state, datetime.now(UTC), session)
        except Exception:
            self._callback_failed = True
            raise _EvidenceFailure("stream evidence callback failed") from None

    def _frame(self, raw: bytes, session: str) -> None:
        try:
            self._on_frame(raw, datetime.now(UTC), session)
        except Exception:
            self._callback_failed = True
            raise _EvidenceFailure("stream evidence callback failed") from None

    async def _bounded(self, operation: Awaitable[_T], deadline: float) -> _T:
        assert self._loop is not None and self._stop_event is not None
        task = asyncio.ensure_future(operation)
        stopper = asyncio.create_task(self._stop_event.wait())
        try:
            remaining = deadline - self._loop.time()
            if self._stop_requested:
                raise _Stopped
            if remaining <= 0:
                raise _DurationElapsed
            done, _ = await asyncio.wait(
                (task, stopper),
                timeout=min(self._timeout, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopper in done:
                raise _Stopped
            if task in done:
                return task.result()
            if self._loop.time() >= deadline:
                raise _DurationElapsed
            raise TimeoutError
        finally:
            task.cancel()
            stopper.cancel()
            await asyncio.gather(task, stopper, return_exceptions=True)

    async def _close(self, sdk: _Sdk, session: str) -> None:
        socket = sdk._ws
        if isinstance(socket, _CapturedSocket):
            socket = socket.socket
        closer = asyncio.create_task(sdk.close())

        def abort() -> None:
            try:
                stop = getattr(getattr(socket, "transport", None), "abort", None)
                if callable(stop):
                    stop()
            except Exception:
                pass

        def consume(task: asyncio.Task[None]) -> None:
            if not task.cancelled():
                task.exception()

        try:
            done, _ = await asyncio.wait((closer,), timeout=self._timeout)
            if not done:
                raise TimeoutError
            closer.result()
        except Exception:
            # wait_for would wait for cancellation acknowledgement. The pinned
            # legacy close shields its connection task, so abort at our own cap.
            abort()
            if not self._callback_failed:
                self._health("ERROR", session)
        finally:
            if not closer.done():
                abort()
                closer.cancel()
                await asyncio.wait((closer,), timeout=self._timeout)
            if closer.done():
                consume(closer)
            else:
                closer.add_done_callback(consume)

    async def _session(self, session: str, deadline: float) -> None:
        sdk: _Sdk | None = None
        try:
            self._health("CONNECTING", session)
            if self._verify_account() != self._account_ref:
                raise PaperTradeStreamError("paper account identity differs")
            # Isolate the websocket logger: DEBUG frame logs could reveal
            # outbound authentication or private incoming account evidence.
            logger = logging.Logger(
                "paper_trade_stream.transport", logging.CRITICAL + 1
            )
            logger.disabled = True
            logger.propagate = False
            sdk = self._sdk_factory(
                self._credentials.key_id.reveal(),
                self._credentials.secret_key.reveal(),
                paper=True,
                raw_data=True,
                websocket_params={
                    "open_timeout": self._timeout,
                    "close_timeout": self._timeout,
                    "ping_interval": self._timeout,
                    "ping_timeout": self._timeout,
                    "max_size": self._max_frame_bytes,
                    "max_queue": 1,
                    "logger": logger,
                },
            )
            if sdk._endpoint != _PAPER_STREAM_URL or sdk._raw_data is not True:
                raise PaperTradeStreamError("SDK paper stream identity differs")
            sdk.subscribe_trade_updates(_unused_handler)
            await self._bounded(sdk._connect(), deadline)
            if sdk._ws is None:
                raise PaperTradeStreamError("SDK did not establish a stream")
            captured = _CapturedSocket(sdk._ws, self, session)
            sdk._ws = captured
            await self._bounded(sdk._auth(), deadline)
            captured.authenticating = False
            self._health("AUTHENTICATED", session)
            await self._bounded(sdk._subscribe_trade_updates(), deadline)
            subscribed = False
            while True:
                await self._bounded(captured.recv(), deadline)
                message = captured.message
                data = cast(dict[str, object], message["data"])
                if message.get("stream") == "listening":
                    streams = data.get("streams")
                    if (
                        not isinstance(streams, list)
                        or not all(isinstance(item, str) for item in streams)
                        or "trade_updates" not in streams
                    ):
                        raise PaperTradeStreamError("subscription was not confirmed")
                    subscribed = True
                    self._health("SUBSCRIBED", session)
                elif (
                    message.get("stream") != "trade_updates"
                    or not subscribed
                    or not isinstance(data.get("event"), str)
                    or not data["event"]
                    or not isinstance(data.get("order"), dict)
                ):
                    raise PaperTradeStreamError("stream message is unexpected")
        except (_Stopped, _DurationElapsed, _EvidenceFailure):
            raise
        except Exception:
            self._health("ERROR", session)
        finally:
            try:
                if sdk is not None:
                    await self._close(sdk, session)
            finally:
                if not self._callback_failed:
                    self._health("DISCONNECTED", session)

    async def run_for(
        self,
        duration_seconds: float,
        *,
        reconnect_delay_seconds: float,
        max_reconnects: int,
    ) -> None:
        """Run at most 1 + max_reconnects sessions; close also has a finite cap."""
        if not isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("stream duration must be positive and finite")
        if not isfinite(reconnect_delay_seconds) or reconnect_delay_seconds < 0:
            raise ValueError("reconnect delay must be nonnegative and finite")
        if type(max_reconnects) is not int or max_reconnects < 0:
            raise ValueError("reconnect count must be a nonnegative integer")
        if self._running:
            raise PaperTradeStreamError("trade stream is already running")
        if self._stop_requested:
            return
        self._running = True
        self._callback_failed = False
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        deadline = self._loop.time() + duration_seconds
        try:
            for attempt in range(max_reconnects + 1):
                await self._session(str(uuid4()), deadline)
                if attempt == max_reconnects or self._stop_requested:
                    break
                remaining = deadline - self._loop.time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=min(reconnect_delay_seconds, remaining),
                    )
                    break
                except TimeoutError:
                    if self._loop.time() >= deadline:
                        break
        except (_Stopped, _DurationElapsed):
            pass
        finally:
            self._running = False
            self._stop_event = None
            self._loop = None
