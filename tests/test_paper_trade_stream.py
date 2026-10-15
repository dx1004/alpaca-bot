import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from alpaca.trading.stream import TradingStream

from alpaca_ross_bot.config import (
    ConfigError,
    PaperConfig,
    PaperCredentials,
    SecretValue,
)
from alpaca_ross_bot.paper_trade_stream import (
    PaperTradeStreamError,
    PaperTradeUpdateStream,
    SdkFactory,
    _Sdk,
)

CONFIG = PaperConfig(
    "https://paper-api.alpaca.markets",
    "https://data.alpaca.markets",
    "PAPER_KEY",
    "PAPER_SECRET",
)
CREDENTIALS = PaperCredentials(SecretValue("fake-key"), SecretValue("fake-secret"))
ACCOUNT = "sha256:synthetic-account"
AUTH = b'{"stream":"authorization","data":{"status":"authorized"}}'
LISTENING = b'{"stream":"listening","data":{"streams":["trade_updates"]}}'
UPDATE = (
    b'{ "stream":"trade_updates", "data":{"event":"fill",'
    b'"price":1.234567890123456789,"order":{"id":"synthetic"}} }'
)


class FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeSocket:
    def __init__(self, frames: list[str | bytes | Exception]) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []
        self.closed = 0
        self.hang_close = False
        self.hang_send_number: int | None = None
        self.transport = FakeTransport()

    async def recv(self) -> str | bytes:
        if not self.frames:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, data: str) -> None:
        self.sent.append(data)
        if len(self.sent) == self.hang_send_number:
            await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed += 1
        if self.hang_close:
            await asyncio.Event().wait()


class Harness:
    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, sockets: list[FakeSocket]
    ) -> None:
        self.sockets = list(sockets)
        self.connect_calls: list[tuple[str, dict[str, object]]] = []
        self.sdk_calls: list[dict[str, object]] = []
        self.verify_calls = 0
        self.frames: list[tuple[bytes, datetime, str]] = []
        self.health: list[tuple[str, datetime, str]] = []
        self.timeline: list[tuple[str, bytes | str, str]] = []
        monkeypatch.setattr(
            "alpaca.trading.stream.websockets_legacy.connect", self.connect
        )

    async def connect(self, endpoint: str, **kwargs: object) -> FakeSocket:
        self.connect_calls.append((endpoint, kwargs))
        return self.sockets.pop(0)

    def sdk_factory(self, *args: object, **kwargs: object) -> _Sdk:
        self.sdk_calls.append(kwargs)
        return cast(SdkFactory, TradingStream)(*args, **kwargs)

    def verify(self) -> str:
        self.verify_calls += 1
        return ACCOUNT

    def frame(self, raw: bytes, seen: datetime, session: str) -> None:
        self.frames.append((raw, seen, session))
        self.timeline.append(("frame", raw, session))

    def state(self, health: str, seen: datetime, session: str) -> None:
        self.health.append((health, seen, session))
        self.timeline.append(("health", health, session))

    def stream(
        self,
        *,
        timeout: float = 0.03,
        cap: int = 512,
        verify: Callable[[], str] | None = None,
        on_frame: Callable[[bytes, datetime, str], None] | None = None,
        on_health: Callable[[str, datetime, str], None] | None = None,
        sdk_factory: SdkFactory | None = None,
    ) -> PaperTradeUpdateStream:
        return PaperTradeUpdateStream(
            CONFIG,
            CREDENTIALS,
            ACCOUNT,
            verify_account=verify or self.verify,
            on_frame=on_frame or self.frame,
            on_health=on_health or self.state,
            timeout_seconds=timeout,
            max_frame_bytes=cap,
            sdk_factory=sdk_factory or self.sdk_factory,
        )


def run(stream: PaperTradeUpdateStream, *, reconnects: int = 0) -> None:
    asyncio.run(
        stream.run_for(0.3, reconnect_delay_seconds=0, max_reconnects=reconnects)
    )


def states(harness: Harness) -> list[str]:
    return [item[0] for item in harness.health]


def test_actual_sdk_requests_exact_paper_url_binary_ack_and_original_bytes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    socket = FakeSocket([AUTH, LISTENING, UPDATE, RuntimeError("private-payload")])
    harness = Harness(monkeypatch, [socket])
    caplog.set_level("DEBUG")
    run(harness.stream())
    assert states(harness) == [
        "CONNECTING",
        "AUTHENTICATED",
        "SUBSCRIBED",
        "ERROR",
        "DISCONNECTED",
    ]
    assert harness.verify_calls == 1
    assert [raw for raw, _, _ in harness.frames] == [AUTH, LISTENING, UPDATE]
    assert all(seen.tzinfo is UTC for _, seen, _ in harness.frames + harness.health)
    session = harness.frames[0][2]
    assert str(UUID(session)) == session
    assert all(item[2] == session for item in harness.frames + harness.health)
    assert harness.timeline.index(("health", "SUBSCRIBED", session)) < (
        harness.timeline.index(("frame", UPDATE, session))
    )
    assert harness.sdk_calls[0]["paper"] is True
    assert harness.sdk_calls[0]["raw_data"] is True
    assert "url_override" not in harness.sdk_calls[0]
    endpoint, params = harness.connect_calls[0]
    assert endpoint == "wss://paper-api.alpaca.markets/stream"
    assert params["max_size"] == 512 and params["max_queue"] == 1
    assert params["open_timeout"] == params["close_timeout"] == 0.03
    assert [json.loads(item) for item in socket.sent] == [
        {
            "action": "authenticate",
            "data": {"key_id": "fake-key", "secret_key": "fake-secret"},
        },
        {"action": "listen", "data": {"streams": ["trade_updates"]}},
    ]
    assert socket.closed == 1
    assert "fake-secret" not in caplog.text and "private-payload" not in caplog.text


def test_text_frames_preserve_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    text = UPDATE.decode().replace("synthetic", "synthetic-\u00e9")
    socket = FakeSocket([AUTH.decode(), LISTENING.decode(), text, RuntimeError()])
    harness = Harness(monkeypatch, [socket])
    run(harness.stream())
    assert harness.frames[2][0] == text.encode("utf-8")
    assert "SUBSCRIBED" in states(harness)


@pytest.mark.parametrize(
    "frames",
    [
        [b'{"stream":"authorization","data":{"status":"unauthorized"}}'],
        [b'{"stream":"wrong","data":{"status":"authorized"}}'],
        [AUTH, b'{"stream":"listening","data":{"streams":[]}}'],
        [AUTH, UPDATE],
        [AUTH, b'{"stream":"unexpected","data":{}}'],
        [AUTH, b"not-json-private-evidence"],
        [AUTH, b'{"stream":"listening","stream":"listening","data":{}}'],
        [AUTH, b'{"stream":"listening","data":{"streams":[NaN]}}'],
        [AUTH, b"[]"],
        [AUTH, b'{"stream":"listening","data":null}'],
    ],
)
def test_invalid_or_pre_subscription_frames_preserved_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, frames: list[bytes]
) -> None:
    socket = FakeSocket(list(frames))
    harness = Harness(monkeypatch, [socket])
    run(harness.stream())
    assert [raw for raw, _, _ in harness.frames] == frames
    assert "SUBSCRIBED" not in states(harness)
    assert states(harness)[-2:] == ["ERROR", "DISCONNECTED"]
    assert socket.closed == 1


@pytest.mark.parametrize("frame", [b"x" * 513, "\u00e9" * 300])
def test_oversized_bytes_or_utf8_are_not_forwarded(
    monkeypatch: pytest.MonkeyPatch, frame: str | bytes
) -> None:
    socket = FakeSocket([AUTH, frame])
    harness = Harness(monkeypatch, [socket])
    run(harness.stream())
    assert [raw for raw, _, _ in harness.frames] == [AUTH]
    assert states(harness)[-2:] == ["ERROR", "DISCONNECTED"]
    assert socket.closed == 1


def test_no_subscription_ack_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    socket = FakeSocket([AUTH])
    harness = Harness(monkeypatch, [socket])
    run(harness.stream(timeout=0.01))
    assert states(harness) == ["CONNECTING", "AUTHENTICATED", "ERROR", "DISCONNECTED"]
    assert len(socket.sent) == 2 and socket.closed == 1


def test_auth_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    socket = FakeSocket([])
    harness = Harness(monkeypatch, [socket])
    run(harness.stream(timeout=0.01))
    assert states(harness) == ["CONNECTING", "ERROR", "DISCONNECTED"]
    assert len(socket.sent) == 1 and socket.closed == 1


@pytest.mark.parametrize("send_number", [1, 2])
def test_auth_and_subscribe_send_are_bounded(
    monkeypatch: pytest.MonkeyPatch, send_number: int
) -> None:
    socket = FakeSocket([AUTH, LISTENING])
    socket.hang_send_number = send_number
    harness = Harness(monkeypatch, [socket])
    run(harness.stream(timeout=0.01))
    assert "SUBSCRIBED" not in states(harness)
    assert states(harness)[-2:] == ["ERROR", "DISCONNECTED"]
    assert len(socket.sent) == send_number and socket.closed == 1


def test_connect_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, [])

    async def stalled_connect(endpoint: str, **kwargs: object) -> FakeSocket:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        "alpaca.trading.stream.websockets_legacy.connect", stalled_connect
    )
    run(harness.stream(timeout=0.01))
    assert states(harness) == ["CONNECTING", "ERROR", "DISCONNECTED"]


@pytest.mark.parametrize(
    "data", [{}, {"event": "fill"}, {"event": "", "order": {}}, {"order": {}}]
)
def test_malformed_trade_update_preserved_but_unhealthy(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, object]
) -> None:
    frame = json.dumps({"stream": "trade_updates", "data": data}).encode()
    socket = FakeSocket([AUTH, LISTENING, frame])
    harness = Harness(monkeypatch, [socket])
    run(harness.stream())
    assert harness.frames[-1][0] == frame
    assert states(harness)[-2:] == ["ERROR", "DISCONNECTED"]


def test_duration_stops_healthy_read_without_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = FakeSocket([AUTH, LISTENING])
    harness = Harness(monkeypatch, [socket])
    stream = harness.stream(timeout=0.1)
    asyncio.run(stream.run_for(0.01, reconnect_delay_seconds=0, max_reconnects=3))
    assert states(harness) == [
        "CONNECTING",
        "AUTHENTICATED",
        "SUBSCRIBED",
        "DISCONNECTED",
    ]
    assert len(harness.connect_calls) == 1 and socket.closed == 1


def test_reconnect_requires_account_check_and_distinct_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets = [
        FakeSocket([AUTH, LISTENING, RuntimeError("private-first")]),
        FakeSocket([AUTH, LISTENING, UPDATE, RuntimeError("private-second")]),
    ]
    harness = Harness(monkeypatch, sockets)
    run(harness.stream(), reconnects=1)
    assert harness.verify_calls == len(harness.connect_calls) == 2
    sessions = [
        session for state, _, session in harness.health if state == "CONNECTING"
    ]
    assert len(sessions) == len(set(sessions)) == 2
    assert all(str(UUID(session)) == session for session in sessions)
    assert all(socket.closed == 1 for socket in sockets)
    assert states(harness).count("DISCONNECTED") == 2


def test_account_mismatch_never_constructs_or_connects_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch, [])
    run(harness.stream(verify=lambda: "wrong-account"))
    assert states(harness) == ["CONNECTING", "ERROR", "DISCONNECTED"]
    assert not harness.sdk_calls and not harness.connect_calls


def test_exact_endpoint_guards_config_and_sdk_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch, [])
    with pytest.raises(ConfigError, match="exact paper"):
        PaperTradeUpdateStream(
            replace(CONFIG, trading_base_url="https://api.alpaca.markets"),
            CREDENTIALS,
            ACCOUNT,
            verify_account=harness.verify,
            on_frame=harness.frame,
            on_health=harness.state,
            timeout_seconds=0.03,
            max_frame_bytes=512,
        )

    def live_factory(*args: object, **kwargs: object) -> _Sdk:
        sdk = harness.sdk_factory(*args, **kwargs)
        sdk._endpoint = "wss://api.alpaca.markets/stream"
        return sdk

    run(harness.stream(sdk_factory=live_factory))
    assert harness.connect_calls == []
    assert states(harness) == ["CONNECTING", "ERROR", "DISCONNECTED"]


@pytest.mark.parametrize("callback", ["frame", "health"])
def test_evidence_failure_propagates_without_retry_or_sensitive_detail(
    monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    socket = FakeSocket([AUTH, LISTENING, UPDATE])
    harness = Harness(monkeypatch, [socket])

    def fail_frame(raw: bytes, seen: datetime, session: str) -> None:
        raise OSError("private callback evidence")

    def fail_health(state: str, seen: datetime, session: str) -> None:
        harness.state(state, seen, session)
        if state == "AUTHENTICATED":
            raise OSError("private callback evidence")

    stream = harness.stream(
        on_frame=fail_frame if callback == "frame" else None,
        on_health=fail_health if callback == "health" else None,
    )
    with pytest.raises(
        PaperTradeStreamError, match="evidence callback failed"
    ) as error:
        run(stream, reconnects=3)
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
    assert harness.verify_calls == len(harness.connect_calls) == 1
    assert socket.closed == 1


def test_stop_interrupts_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = FakeSocket([AUTH, LISTENING])
    harness = Harness(monkeypatch, [socket])
    stream = harness.stream(timeout=1)

    async def exercise() -> None:
        task = asyncio.create_task(
            stream.run_for(30, reconnect_delay_seconds=30, max_reconnects=3)
        )
        while "SUBSCRIBED" not in states(harness):
            await asyncio.sleep(0)
        stream.stop()
        await asyncio.wait_for(task, 0.1)

    asyncio.run(exercise())
    assert states(harness)[-1] == "DISCONNECTED"
    assert socket.closed == 1 and len(harness.connect_calls) == 1


def test_stop_before_run_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, [])
    stream = harness.stream()
    stream.stop()
    run(stream)
    assert harness.health == [] and harness.verify_calls == 0


def test_stop_interrupts_pending_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, [])
    connecting = asyncio.Event()

    async def stalled_connect(endpoint: str, **kwargs: object) -> FakeSocket:
        connecting.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        "alpaca.trading.stream.websockets_legacy.connect", stalled_connect
    )
    stream = harness.stream(timeout=30)

    async def exercise() -> None:
        task = asyncio.create_task(
            stream.run_for(30, reconnect_delay_seconds=30, max_reconnects=3)
        )
        await asyncio.wait_for(connecting.wait(), 0.1)
        stream.stop()
        await asyncio.wait_for(task, 0.1)

    asyncio.run(exercise())
    assert states(harness) == ["CONNECTING", "DISCONNECTED"]


def test_stop_interrupts_reconnect_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    socket = FakeSocket([AUTH, LISTENING, RuntimeError()])
    harness = Harness(monkeypatch, [socket])
    disconnected = asyncio.Event()

    def on_health(state: str, seen: datetime, session: str) -> None:
        harness.state(state, seen, session)
        if state == "DISCONNECTED":
            disconnected.set()

    stream = harness.stream(timeout=1, on_health=on_health)

    async def exercise() -> None:
        task = asyncio.create_task(
            stream.run_for(30, reconnect_delay_seconds=30, max_reconnects=3)
        )
        await asyncio.wait_for(disconnected.wait(), 0.1)
        await asyncio.sleep(0)
        stream.stop()
        await asyncio.wait_for(task, 0.1)

    asyncio.run(exercise())
    assert len(harness.connect_calls) == 1 and socket.closed == 1


def test_external_cancellation_still_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = FakeSocket([AUTH, LISTENING])
    harness = Harness(monkeypatch, [socket])
    stream = harness.stream(timeout=30)

    async def exercise() -> None:
        task = asyncio.create_task(
            stream.run_for(30, reconnect_delay_seconds=0, max_reconnects=3)
        )
        while "SUBSCRIBED" not in states(harness):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert states(harness)[-1] == "DISCONNECTED" and socket.closed == 1


def test_close_has_independent_timeout_and_aborts_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = FakeSocket([AUTH, RuntimeError("private disconnect")])
    socket.hang_close = True
    harness = Harness(monkeypatch, [socket])

    async def exercise() -> None:
        await asyncio.wait_for(
            harness.stream(timeout=0.01).run_for(
                0.1, reconnect_delay_seconds=0, max_reconnects=0
            ),
            0.1,
        )

    asyncio.run(exercise())
    assert socket.closed == 1 and socket.transport.aborted
    assert states(harness)[-1] == "DISCONNECTED"


def test_close_aborts_before_cancellation_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model legacy close shielding its connection task after cancellation."""
    aborted = asyncio.Event()

    class Transport(FakeTransport):
        def abort(self) -> None:
            super().abort()
            aborted.set()

    class Socket(FakeSocket):
        async def close(self) -> None:
            self.closed += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await aborted.wait()

    socket = Socket([AUTH, RuntimeError("private disconnect")])
    socket.transport = Transport()
    harness = Harness(monkeypatch, [socket])

    async def exercise() -> None:
        task = asyncio.create_task(
            harness.stream(timeout=0.01).run_for(
                0.1, reconnect_delay_seconds=0, max_reconnects=0
            )
        )
        done, _ = await asyncio.wait((task,), timeout=0.1)
        if not done:
            aborted.set()
            await task
            pytest.fail("close waited for cancellation before aborting transport")
        await task

    asyncio.run(exercise())
    assert socket.transport.aborted and socket.closed == 1
    assert states(harness)[-1] == "DISCONNECTED"
