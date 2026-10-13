import asyncio
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.feed_health import FeedKind, HealthReason
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.stream_protocol import AlpacaJsonStream, StreamProtocolError


class FakeSocket:
    def __init__(self, frames: list[str], *, ping_hangs: bool = False) -> None:
        self.frames = deque(frames)
        self.sent: list[str] = []
        self.closed = False
        self.ping_hangs = ping_hangs

    async def recv(self) -> str:
        if not self.frames:
            await asyncio.Future[str]()
        return self.frames.popleft()

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> asyncio.Future[float]:
        pong = asyncio.get_running_loop().create_future()
        if not self.ping_hangs:
            pong.set_result(0.0)
        return pong


class FakeConnector:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.kwargs: dict[str, Any] = {}
        self.endpoint = ""

    async def __call__(self, endpoint: str, **kwargs: Any) -> FakeSocket:
        self.endpoint = endpoint
        self.kwargs = kwargs
        return self.socket


def _client(
    frames: list[str],
    feed: FeedKind = FeedKind.EQUITIES,
    *,
    heartbeat_interval_seconds: float = 60,
    heartbeat_timeout_seconds: float = 5,
) -> tuple[AlpacaJsonStream, FakeSocket, FakeConnector]:
    socket = FakeSocket(frames)
    connector = FakeConnector(socket)
    credentials = PaperCredentials(
        SecretValue("paper-key"), SecretValue("paper-secret")
    )
    return (
        AlpacaJsonStream(
            feed,
            credentials,
            timeout_seconds=0.1,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            connector=connector,
        ),
        socket,
        connector,
    )


def test_iex_connection_requires_auth_and_exact_subscription_ack() -> None:
    async def run() -> None:
        client, socket, connector = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
                '[{"T":"t","S":"AAPL","p":1}]',
            ]
        )
        await client.open()
        assert connector.endpoint == "wss://stream.data.alpaca.markets/v2/iex"
        assert connector.kwargs["additional_headers"] == {
            "APCA-API-KEY-ID": "paper-key",
            "APCA-API-SECRET-KEY": "paper-secret",
        }
        assert client.identity.coverage_label == "NOT_SIP_EQUIVALENT"
        with pytest.raises(StreamProtocolError):
            await client.receive()
        await client.subscribe({"trades": ("AAPL",)})
        sent = json.loads(socket.sent[0])
        assert sent == {"action": "subscribe", "trades": ["AAPL"]}
        received = await client.receive()
        assert received.raw == '[{"T":"t","S":"AAPL","p":1}]'
        assert received.first_seen_at.tzinfo == UTC
        await client.close()
        assert socket.closed

    asyncio.run(run())


def test_subscription_mismatch_closes_and_never_marks_subscribed() -> None:
    async def run() -> None:
        client, socket, _ = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":[]}]',
            ]
        )
        await client.open()
        with pytest.raises(StreamProtocolError, match="differs from request"):
            await client.subscribe({"trades": ("AAPL",)})
        assert not client.subscribed
        assert socket.closed

    asyncio.run(run())


def test_news_feed_is_separate_and_feed_channels_are_scoped() -> None:
    async def run() -> None:
        client, _, connector = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","news":["AAPL"]}]',
            ],
            FeedKind.NEWS,
        )
        await client.open()
        await client.subscribe({"news": ("AAPL",)})
        assert connector.endpoint.endswith("/v1beta1/news")
        await client.close()

    asyncio.run(run())


def test_auth_failure_and_timeout_fail_closed() -> None:
    async def run() -> None:
        client, socket, _ = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"error","code":401,"msg":"redacted detail"}]',
            ]
        )
        with pytest.raises(StreamProtocolError, match="code 401"):
            await client.open()
        assert socket.closed
        assert "redacted detail" not in repr(client)

        timeout_client, timeout_socket, _ = _client(
            ['[{"T":"success","msg":"connected"}]']
        )
        with pytest.raises(TimeoutError):
            await timeout_client.open()
        assert timeout_socket.closed

    asyncio.run(run())


def test_protocol_error_after_subscription_closes_stream() -> None:
    async def run() -> None:
        client, socket, _ = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
                '[{"T":"error","code":407,"msg":"slow client"}]',
            ]
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        with pytest.raises(StreamProtocolError, match="code 407"):
            await client.receive()
        assert socket.closed
        assert not client.subscribed

    asyncio.run(run())


def test_wrong_feed_data_closes_stream() -> None:
    async def run() -> None:
        client, socket, _ = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
                '[{"T":"n","symbol":"AAPL"}]',
            ]
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        with pytest.raises(StreamProtocolError, match="does not match feed"):
            await client.receive()
        assert socket.closed

    asyncio.run(run())


def test_reconnect_increments_epoch_and_keeps_gap_unresolved() -> None:
    async def run() -> None:
        first = FakeSocket(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
            ]
        )
        second = FakeSocket(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
            ]
        )

        class ReconnectConnector:
            sockets = deque([first, second])

            async def __call__(self, endpoint: str, **kwargs: Any) -> FakeSocket:
                del endpoint, kwargs
                return self.sockets.popleft()

        credentials = PaperCredentials(
            SecretValue("paper-key"), SecretValue("paper-secret")
        )
        client = AlpacaJsonStream(
            FeedKind.EQUITIES,
            credentials,
            timeout_seconds=0.1,
            heartbeat_interval_seconds=60,
            heartbeat_timeout_seconds=5,
            connector=ReconnectConnector(),
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        assert client.connection_epoch == client.subscribed_epoch == 1
        assert client.connected
        assert not client.gap_unresolved
        await client.close()
        assert client.gap_unresolved
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        assert client.connection_epoch == client.subscribed_epoch == 2
        assert client.gap_unresolved
        await client.close()

    asyncio.run(run())


def test_transport_pong_and_data_timestamps_feed_health_snapshot() -> None:
    async def run() -> None:
        client, _, _ = _client(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
                '[{"T":"t","S":"AAPL","p":1}]',
            ],
            heartbeat_interval_seconds=0.001,
            heartbeat_timeout_seconds=0.1,
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        assert client.last_heartbeat_at is None
        await asyncio.sleep(0.01)
        frame = await client.receive()
        snapshot = client.health_snapshot()
        assert client.last_heartbeat_at is not None
        assert snapshot.last_heartbeat_at == client.last_heartbeat_at
        assert snapshot.last_data_at == frame.first_seen_at
        assert not snapshot.reasons(
            now=datetime.now(UTC),
            max_heartbeat_age=timedelta(seconds=1),
            max_data_age=timedelta(seconds=1),
        )
        await client.close()
        reasons = client.health_snapshot().reasons(
            now=datetime.now(UTC),
            max_heartbeat_age=timedelta(seconds=1),
            max_data_age=timedelta(seconds=1),
        )
        assert HealthReason.DISCONNECTED in reasons
        assert HealthReason.GAP_UNRESOLVED in reasons

    asyncio.run(run())


def test_missing_transport_pong_opens_gap_and_invalidates_subscription() -> None:
    async def run() -> None:
        socket = FakeSocket(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
            ],
            ping_hangs=True,
        )
        connector = FakeConnector(socket)
        client = AlpacaJsonStream(
            FeedKind.EQUITIES,
            PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
            timeout_seconds=0.1,
            heartbeat_interval_seconds=0.001,
            heartbeat_timeout_seconds=0.005,
            connector=connector,
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        await asyncio.sleep(0.02)
        assert not client.connected
        assert not client.subscribed
        assert client.gap_unresolved
        assert socket.closed

    asyncio.run(run())


def test_raw_frame_and_unresolved_gap_survive_journal_restart(tmp_path: Path) -> None:
    async def run() -> None:
        runtime = tmp_path / "var"
        path = runtime / "journal.sqlite3"
        journal = Journal(path, runtime)
        socket = FakeSocket(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
                '[{"T":"t","S":"AAPL","p":1}]',
            ]
        )
        client = AlpacaJsonStream(
            FeedKind.EQUITIES,
            PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
            timeout_seconds=0.1,
            heartbeat_interval_seconds=60,
            heartbeat_timeout_seconds=5,
            connector=FakeConnector(socket),
            journal=journal,
        )
        await client.open()
        await client.subscribe({"trades": ("AAPL",)})
        received = await client.receive()
        raw_event = journal.read_event(2)
        assert raw_event is not None
        expected_payload = (
            received.raw.encode("utf-8")
            if isinstance(received.raw, str)
            else received.raw
        )
        assert raw_event[1] == expected_payload
        assert journal.read_state("feed_session_active:equities") is True
        heartbeat_task = client._heartbeat_task
        assert heartbeat_task is not None
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await socket.close()
        journal.close()

        reopened = Journal(path, runtime)
        next_socket = FakeSocket(
            [
                '[{"T":"success","msg":"connected"}]',
                '[{"T":"success","msg":"authenticated"}]',
                '[{"T":"subscription","trades":["AAPL"]}]',
            ]
        )
        resumed = AlpacaJsonStream(
            FeedKind.EQUITIES,
            PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
            timeout_seconds=0.1,
            heartbeat_interval_seconds=60,
            heartbeat_timeout_seconds=5,
            connector=FakeConnector(next_socket),
            journal=reopened,
        )
        assert reopened.read_state("feed_gap:equities") == {
            "connection_epoch": 0,
            "reason": "previous_process_session_unclosed",
            "unresolved": True,
        }
        assert reopened.read_state("feed_session_active:equities") is False
        gap_event = reopened.read_event(3)
        assert gap_event is not None
        assert json.loads(gap_event[1])["continuity_proven"] is False
        assert resumed.gap_unresolved
        await resumed.open()
        await resumed.subscribe({"trades": ("AAPL",)})
        assert resumed.gap_unresolved
        assert HealthReason.GAP_UNRESOLVED in resumed.health_snapshot().reasons(
            now=datetime.now(UTC),
            max_heartbeat_age=timedelta(seconds=1),
            max_data_age=timedelta(seconds=1),
        )
        await resumed.close()
        reopened.close()

    asyncio.run(run())
