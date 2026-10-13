"""Observable Alpaca JSON stream handshake and subscription acknowledgments.

This module establishes transport state only. It does not provide data
eligibility, reconnect recovery, or proof that an IEX stream covers the market.
Protocol: https://docs.alpaca.markets/us/docs/streaming-market-data
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol

from websockets.asyncio.client import connect

from alpaca_ross_bot.config import PaperCredentials
from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal

_ENDPOINTS = {
    FeedKind.EQUITIES: "wss://stream.data.alpaca.markets/v2/iex",
    FeedKind.NEWS: "wss://stream.data.alpaca.markets/v1beta1/news",
}


class StreamProtocolError(ValueError):
    """The stream did not confirm the expected protocol state."""


class Socket(Protocol):
    async def recv(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...

    async def close(self) -> None: ...

    def ping(self) -> Awaitable[Awaitable[float]]: ...


type Connector = Callable[..., Awaitable[Socket]]


@dataclass(frozen=True)
class StreamIdentity:
    feed: FeedKind
    endpoint: str
    coverage_label: str


@dataclass(frozen=True)
class ReceivedFrame:
    raw: str | bytes
    first_seen_at: datetime


class AlpacaJsonStream:
    """One explicit connection; reconnects must be created and audited by caller."""

    def __init__(
        self,
        feed: FeedKind,
        credentials: PaperCredentials,
        *,
        timeout_seconds: float,
        heartbeat_interval_seconds: float,
        heartbeat_timeout_seconds: float,
        connector: Connector | None = None,
        journal: Journal | None = None,
    ) -> None:
        if not isinstance(feed, FeedKind):
            raise ValueError("unsupported feed")
        if any(
            not isfinite(value) or value <= 0
            for value in (
                timeout_seconds,
                heartbeat_interval_seconds,
                heartbeat_timeout_seconds,
            )
        ):
            raise ValueError("stream timing limits must be positive and finite")
        self.identity = StreamIdentity(
            feed=feed,
            endpoint=_ENDPOINTS[feed],
            coverage_label=(
                "NOT_SIP_EQUIVALENT"
                if feed is FeedKind.EQUITIES
                else "SEPARATE_ALPACA_NEWS_STREAM"
            ),
        )
        self._credentials = credentials
        self._timeout = timeout_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._connector = connector
        self._journal = journal
        self._socket: Socket | None = None
        self._subscribed: dict[str, tuple[str, ...]] | None = None
        self._connection_epoch = 0
        self._subscribed_epoch: int | None = None
        self._connected = False
        self._gap_unresolved = self._load_gap_state()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_data_at: datetime | None = None
        session_active = (
            None
            if self._journal is None
            else self._journal.read_state(self._session_state_key)
        )
        if session_active is not None and type(session_active) is not bool:
            raise StreamProtocolError("stored feed session state is invalid")
        if session_active is True:
            self._mark_gap("previous_process_session_unclosed")

    @property
    def subscribed(self) -> bool:
        return self._subscribed is not None

    @property
    def connection_epoch(self) -> int:
        return self._connection_epoch

    @property
    def subscribed_epoch(self) -> int | None:
        return self._subscribed_epoch

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def gap_unresolved(self) -> bool:
        return self._gap_unresolved

    @property
    def last_heartbeat_at(self) -> datetime | None:
        """Local receipt time of the latest successful transport pong."""
        return self._last_heartbeat_at

    @property
    def last_data_at(self) -> datetime | None:
        return self._last_data_at

    def health_snapshot(self) -> FeedHealth:
        """Expose transport evidence without deciding its freshness limits."""
        if self._connection_epoch < 1:
            raise StreamProtocolError("no connection epoch has been observed")
        return FeedHealth(
            kind=self.identity.feed,
            feed="iex" if self.identity.feed is FeedKind.EQUITIES else "alpaca_news",
            connection_epoch=self._connection_epoch,
            subscribed_epoch=self._subscribed_epoch,
            connected=self._connected,
            gap_unresolved=self._gap_unresolved,
            last_heartbeat_at=self._last_heartbeat_at,
            last_data_at=self._last_data_at,
        )

    async def open(self) -> None:
        """Connect, then require Alpaca's connected and authenticated frames."""
        if self._socket is not None:
            raise StreamProtocolError("stream is already open")
        socket = await self._connect()
        self._connection_epoch += 1
        self._socket = socket
        self._last_heartbeat_at = None
        try:
            await self._expect_success(socket, "connected")
            await self._expect_success(socket, "authenticated")
            self._connected = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(socket))
        except Exception:
            await socket.close()
            self._socket = None
            self._connected = False
            raise

    async def subscribe(self, channels: Mapping[str, tuple[str, ...]]) -> None:
        """Request an explicit subscription and verify the server's full state."""
        socket = self._require_socket()
        if self._subscribed is not None:
            raise StreamProtocolError("subscription already confirmed")
        expected = _normalize_subscriptions(channels, self.identity.feed)
        try:
            self._mark_session_attempted()
            await socket.send(
                json.dumps(
                    {"action": "subscribe", **{k: list(v) for k, v in expected.items()}}
                )
            )
            raw = await asyncio.wait_for(socket.recv(), timeout=self._timeout)
            message = _single_message(raw)
            if message.get("T") == "error":
                raise _server_error(message)
            if message.get("T") != "subscription":
                raise StreamProtocolError("subscription acknowledgment missing")
            acknowledged = _normalize_ack(message, expected)
            if acknowledged != expected:
                raise StreamProtocolError(
                    "subscription acknowledgment differs from request"
                )
            self._subscribed = expected
            self._subscribed_epoch = self._connection_epoch
        except Exception:
            self._gap_unresolved = True
            await self.close()
            raise

    async def close(self) -> None:
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None and heartbeat_task is not asyncio.current_task():
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        socket = self._socket
        gap_error: Exception | None = None
        if self._subscribed_epoch == self._connection_epoch:
            try:
                self._mark_gap("stream_closed_after_subscription")
            except Exception as exc:
                gap_error = exc
        self._socket = None
        self._subscribed = None
        self._subscribed_epoch = None
        self._connected = False
        if socket is not None:
            await socket.close()
        if gap_error is not None:
            raise StreamProtocolError(
                "could not durably record feed gap"
            ) from gap_error

    async def receive(self) -> ReceivedFrame:
        """Receive one raw frame after subscription acknowledgment only."""
        if self._subscribed is None:
            raise StreamProtocolError(
                "data is unavailable before subscription confirmation"
            )
        try:
            raw = await asyncio.wait_for(
                self._require_socket().recv(), timeout=self._timeout
            )
            first_seen_at = datetime.now(UTC)
            _validate_data_frame(raw, self.identity.feed)
            if self._journal is not None:
                payload = raw.encode("utf-8") if isinstance(raw, str) else raw
                self._journal.append_next(
                    source_id="alpaca_market_data",
                    feed=(
                        "iex"
                        if self.identity.feed is FeedKind.EQUITIES
                        else "alpaca_news"
                    ),
                    event_kind="RAW_FRAME",
                    payload=payload,
                    system_first_seen_at=first_seen_at,
                    processed_at=datetime.now(UTC),
                )
            self._last_data_at = first_seen_at
        except Exception:
            await self.close()
            raise
        return ReceivedFrame(raw=raw, first_seen_at=first_seen_at)

    async def _heartbeat_loop(self, socket: Socket) -> None:
        try:
            while self._socket is socket and self._connected:
                await asyncio.sleep(self._heartbeat_interval)
                deadline = time.monotonic() + self._heartbeat_timeout
                pong_waiter = await asyncio.wait_for(
                    socket.ping(), timeout=self._heartbeat_timeout
                )
                await asyncio.wait_for(pong_waiter, timeout=deadline - time.monotonic())
                self._last_heartbeat_at = datetime.now(UTC)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._socket is socket:
                if self._subscribed_epoch == self._connection_epoch:
                    try:
                        self._mark_gap("websocket_heartbeat_failed")
                    except Exception:
                        # The in-memory veto remains active if durable storage fails.
                        self._gap_unresolved = True
                self._socket = None
                self._subscribed = None
                self._subscribed_epoch = None
                self._connected = False
                self._heartbeat_task = None
                await socket.close()

    def _load_gap_state(self) -> bool:
        if self._journal is None:
            return False
        value = self._journal.read_state(self._gap_state_key)
        if value is None:
            return False
        if not isinstance(value, dict) or value.get("unresolved") is not True:
            raise StreamProtocolError("stored feed gap state is invalid")
        return True

    @property
    def _gap_state_key(self) -> str:
        return f"feed_gap:{self.identity.feed.value.lower()}"

    @property
    def _session_state_key(self) -> str:
        return f"feed_session_active:{self.identity.feed.value.lower()}"

    def _mark_gap(self, reason: str) -> None:
        self._gap_unresolved = True
        if self._journal is None:
            return
        now = datetime.now(UTC)
        payload = json.dumps(
            {
                "feed": self.identity.feed.value,
                "reason": reason,
                "connection_epoch": self._connection_epoch,
                "continuity_proven": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._journal.append_next(
            source_id="alpaca_market_data",
            feed=("iex" if self.identity.feed is FeedKind.EQUITIES else "alpaca_news"),
            event_kind="FEED_GAP",
            payload=payload,
            system_first_seen_at=now,
            processed_at=now,
            state_updates={
                self._gap_state_key: {
                    "unresolved": True,
                    "reason": reason,
                    "connection_epoch": self._connection_epoch,
                },
                self._session_state_key: False,
            },
        )

    def _mark_session_attempted(self) -> None:
        if self._journal is None:
            return
        now = datetime.now(UTC)
        self._journal.append_next(
            source_id="alpaca_market_data",
            feed=("iex" if self.identity.feed is FeedKind.EQUITIES else "alpaca_news"),
            event_kind="FEED_SESSION_ATTEMPTED",
            payload=json.dumps(
                {
                    "feed": self.identity.feed.value,
                    "connection_epoch": self._connection_epoch,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            system_first_seen_at=now,
            processed_at=now,
            state_updates={self._session_state_key: True},
        )

    async def _connect(self) -> Socket:
        if self._connector is None:
            return await connect(
                self.identity.endpoint,
                additional_headers=self._credentials.auth_headers(),
                open_timeout=self._timeout,
                close_timeout=self._timeout,
                ping_interval=None,
                max_size=1_048_576,
            )
        result = self._connector(
            self.identity.endpoint,
            additional_headers=self._credentials.auth_headers(),
            open_timeout=self._timeout,
            close_timeout=self._timeout,
            ping_interval=None,
            max_size=1_048_576,
        )
        return await result

    async def _expect_success(self, socket: Socket, expected: str) -> None:
        raw = await asyncio.wait_for(socket.recv(), timeout=self._timeout)
        message = _single_message(raw)
        if message.get("T") == "error":
            raise _server_error(message)
        if message.get("T") != "success" or message.get("msg") != expected:
            raise StreamProtocolError(f"{expected} confirmation missing")

    def _require_socket(self) -> Socket:
        if self._socket is None:
            raise StreamProtocolError("stream is not connected")
        return self._socket


def _single_message(raw: str | bytes) -> dict[str, object]:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StreamProtocolError("invalid JSON stream frame") from exc
    if not isinstance(decoded, list) or len(decoded) != 1:
        raise StreamProtocolError("control frame must contain exactly one message")
    decoded = decoded[0]
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) for key in decoded
    ):
        raise StreamProtocolError("unexpected stream frame shape")
    return decoded


def _validate_data_frame(raw: str | bytes, feed: FeedKind) -> None:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StreamProtocolError("invalid JSON data frame") from exc
    if not isinstance(decoded, list) or not decoded:
        raise StreamProtocolError("data frame must contain one or more messages")
    for message in decoded:
        if not isinstance(message, dict) or not isinstance(message.get("T"), str):
            raise StreamProtocolError("unexpected data frame shape")
        if message["T"] == "error":
            raise _server_error(message)
        if message["T"] in {"success", "subscription"}:
            raise StreamProtocolError("unexpected protocol control in data stream")
        if (feed is FeedKind.NEWS) != (message["T"] == "n"):
            raise StreamProtocolError("stream data message does not match feed")


def _server_error(message: Mapping[str, object]) -> StreamProtocolError:
    code = message.get("code")
    if type(code) is not int:
        return StreamProtocolError("Alpaca returned an unclassified stream error")
    return StreamProtocolError(f"Alpaca stream error code {code}")


def _normalize_subscriptions(
    channels: Mapping[str, tuple[str, ...]], feed: FeedKind
) -> dict[str, tuple[str, ...]]:
    allowed = (
        {"news"}
        if feed is FeedKind.NEWS
        else {
            "trades",
            "quotes",
            "bars",
            "updatedBars",
            "dailyBars",
            "statuses",
            "lulds",
        }
    )
    normalized: dict[str, tuple[str, ...]] = {}
    if not channels:
        raise StreamProtocolError("empty subscription is not allowed")
    for channel, symbols in channels.items():
        if channel not in allowed or not symbols:
            raise StreamProtocolError("subscription channel is unsupported or empty")
        if any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
            raise StreamProtocolError("subscription symbols must be nonempty text")
        if len(set(symbols)) != len(symbols):
            raise StreamProtocolError("subscription symbols must be unique")
        normalized[channel] = tuple(sorted(symbols))
    return normalized


def _normalize_ack(
    message: Mapping[str, object], expected: Mapping[str, tuple[str, ...]]
) -> dict[str, tuple[str, ...]]:
    acknowledged: dict[str, tuple[str, ...]] = {}
    for channel in expected:
        values = message.get(channel)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise StreamProtocolError("subscription acknowledgment has invalid fields")
        acknowledged[channel] = tuple(sorted(values))
    for channel, values in message.items():
        if channel in {"T"} or channel in expected:
            continue
        if not isinstance(values, list) or values:
            raise StreamProtocolError("server acknowledged an unexpected subscription")
    return acknowledged
