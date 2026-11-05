"""Single-request Alpaca paper order transport; no retry or order policy."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from math import isfinite
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import UUID

from alpaca_ross_bot.config import (
    PAPER_TRADING_URL,
    ConfigError,
    PaperConfig,
    PaperCredentials,
)
from alpaca_ross_bot.journal import Journal, JournalError, OrderHttpObservation
from alpaca_ross_bot.paper_order_contract import (
    OrderSnapshot,
    PaperContractError,
    decode_broker_json,
    paper_account_ref,
    require_submission_eligible_account,
    validate_order_snapshot,
    validate_saved_request,
)

_PAPER_HOST = "paper-api.alpaca.markets"

if TYPE_CHECKING:
    from .native_oco_http import NativeOcoHttp
    from .paper_engineering import EngineeringHarness
    from .source_close_http import SourceCloseHttp


class PaperOrderHttpError(ValueError):
    """A paper request failed without establishing its broker outcome."""


class PaperOrderAuthenticationError(PaperOrderHttpError):
    """Paper API credentials or account authorization could not be verified."""


@dataclass(frozen=True)
class PaperHttpResponse:
    status: int
    body: bytes


type Exchange = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int], PaperHttpResponse
]


class PaperOrderHttp:
    """Transport one request to the exact paper host without automatic replay.

    A caller must persist intent and a send-attempt marker before calling POST.
    Any exception or unclear HTTP response is an unknown order outcome.
    """

    def __init__(
        self,
        config: PaperConfig,
        credentials: PaperCredentials,
        paper_account_ref: str | None = None,
        *,
        timeout_seconds: float,
        max_request_bytes: int,
        max_response_bytes: int,
        exchange: Exchange | None = None,
    ) -> None:
        if config.trading_base_url != PAPER_TRADING_URL:
            raise ConfigError("order transport requires the exact paper endpoint")
        if paper_account_ref is not None and not paper_account_ref.strip():
            raise ValueError("paper account reference must be nonempty")
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("order request timeout must be positive")
        if (
            type(max_request_bytes) is not int
            or max_request_bytes < 1
            or type(max_response_bytes) is not int
            or max_response_bytes < 1
        ):
            raise ValueError("order request and response caps must be positive")
        self._credentials = credentials
        self._paper_account_ref = paper_account_ref
        self._timeout = timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._exchange = exchange or _exchange_paper_https

    @property
    def native_http_origin(self) -> str:
        return (
            "BUILTIN_PAPER_HTTPS"
            if self._exchange is _exchange_paper_https
            else "INJECTED_EXCHANGE"
        )

    def validate_native_http_body(self, body: str) -> None:
        """Only a canonical native preparation can supply this bounded body."""
        if (
            type(body) is not str
            or not 0 < len(body.encode()) <= self._max_request_bytes
        ):
            raise PaperOrderHttpError("native HTTP request exceeds its cap")

    def post_native_oco_once(
        self,
        owner: NativeOcoHttp,
        harness: EngineeringHarness,
        *,
        formal_api: PaperOrderHttp,
        group_id: str,
        decision_at: datetime,
        started_at: datetime,
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        """Native engineering transport; no CLI, strategy or cleanup authority."""
        from .native_oco_http import NativeOcoHttp

        if type(owner) is not NativeOcoHttp:
            raise PaperOrderHttpError("canonical native HTTP owner required")
        return owner.post_once(
            self,
            harness,
            formal_api=formal_api,
            group_id=group_id,
            decision_at=decision_at,
            started_at=started_at,
            clock=clock,
        )

    def validate_source_close_http_body(self, body: str) -> None:
        """Validate only the existing bounded simple source-close request shape."""
        if (
            type(body) is not str
            or not 0 < len(body.encode()) <= self._max_request_bytes
        ):
            raise PaperOrderHttpError("source HTTP request exceeds its cap")
        request = validate_saved_request(body)
        if request["side"] != "sell" or request["type"] != "market":
            raise PaperOrderHttpError(
                "source HTTP requires the simple market sell shape"
            )

    def post_source_close_once(
        self,
        owner: SourceCloseHttp,
        harness: EngineeringHarness,
        *,
        formal_api: PaperOrderHttp,
        client_order_id: str,
        attempt_id: str,
        preparation: dict[str, Any],
        decision_at: datetime,
        started_at: datetime,
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        """Manual synthetic source transport; builtin dispatch remains disabled."""
        from .source_close_http import SourceCloseHttp

        if type(owner) is not SourceCloseHttp:
            raise PaperOrderHttpError("canonical source HTTP owner required")
        return owner.post_once(
            self,
            harness,
            formal_api=formal_api,
            client_order_id=client_order_id,
            attempt_id=attempt_id,
            preparation=preparation,
            decision_at=decision_at,
            started_at=started_at,
            clock=clock,
        )

    def verify_account(
        self,
        journal: Journal,
        *,
        for_submission: bool = False,
        expected_reference: str | None = None,
    ) -> str:
        """Authenticate against the exact paper host and bind the private journal."""
        journal.require_private_order_state()
        response = self._request("GET", "/v2/account", None)
        if response.status in (401, 403):
            raise PaperOrderAuthenticationError("paper account authentication failed")
        if response.status != 200:
            raise PaperOrderHttpError("paper account verification failed")
        try:
            reference = paper_account_ref(response.body)
        except PaperContractError as exc:
            raise PaperOrderHttpError("paper account verification failed") from exc
        if self._paper_account_ref is not None and reference != self._paper_account_ref:
            raise PaperOrderHttpError("authenticated paper account identity differs")
        if expected_reference is not None and reference != expected_reference:
            raise PaperOrderHttpError("authenticated paper account identity differs")
        journal.record_paper_account_verification(
            reference, response.body, datetime.now(UTC)
        )
        if for_submission:
            try:
                require_submission_eligible_account(response.body)
            except PaperContractError as exc:
                raise PaperOrderHttpError(
                    "paper account submission is blocked"
                ) from exc
        return reference

    def post_reserved_order_once(
        self, journal: Journal, client_order_id: str, started_at: datetime
    ) -> PaperHttpResponse:
        """Consume one durable send attempt, then hand its saved request to HTTP."""
        journal.require_private_order_state()
        intent = journal.read_order_intent(client_order_id)
        if not isinstance(intent, dict) or set(intent) != {
            "paper_endpoint",
            "paper_account_ref",
            "purpose",
            "request_body",
        }:
            raise PaperOrderHttpError("reserved paper order intent is invalid")
        if (
            intent["paper_endpoint"] != PAPER_TRADING_URL
            or not isinstance(intent["paper_account_ref"], str)
            or not intent["paper_account_ref"].strip()
            or (
                self._paper_account_ref is not None
                and intent["paper_account_ref"] != self._paper_account_ref
            )
            or intent["purpose"] != "ENGINEERING_TEST"
        ):
            raise PaperOrderHttpError("reserved paper order intent identity is invalid")
        request_body = intent["request_body"]
        if not isinstance(request_body, str):
            raise PaperOrderHttpError("reserved paper order request is invalid")
        body = request_body.encode("utf-8")
        if not body or len(body) > self._max_request_bytes:
            raise PaperOrderHttpError("reserved paper order request exceeds its cap")
        try:
            parsed = json.loads(body)
            canonical = json.dumps(
                parsed, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise PaperOrderHttpError(
                "reserved paper order request is invalid"
            ) from exc
        if canonical != body:
            raise PaperOrderHttpError("reserved paper order request is not canonical")
        if (
            not isinstance(parsed, dict)
            or parsed.get("client_order_id") != client_order_id
        ):
            raise PaperOrderHttpError("reserved paper order client ID is invalid")
        try:
            validate_saved_request(request_body)
        except PaperContractError as exc:
            raise PaperOrderHttpError(
                "reserved engineering order shape is invalid"
            ) from exc
        if journal.has_order_send_attempt(client_order_id):
            raise JournalError("order send was already attempted")
        reference = self.verify_account(
            journal, for_submission=True, expected_reference=intent["paper_account_ref"]
        )
        if intent["paper_account_ref"] != reference:
            raise PaperOrderHttpError("reserved paper order intent identity is invalid")
        journal.mark_order_send_attempt(client_order_id, started_at)
        return self._request_and_record(
            journal, client_order_id, "POST", "/v2/orders", body
        )

    def get_order_by_client_id(
        self, journal: Journal, client_order_id: str
    ) -> PaperHttpResponse:
        if not client_order_id or not client_order_id.strip():
            raise ValueError("client order ID must be nonempty")
        journal.require_private_order_state()
        intent = journal.read_order_intent(client_order_id)
        if (
            not isinstance(intent, dict)
            or intent.get("paper_endpoint") != PAPER_TRADING_URL
            or not isinstance(intent.get("paper_account_ref"), str)
            or (
                self._paper_account_ref is not None
                and intent.get("paper_account_ref") != self._paper_account_ref
            )
            or intent.get("purpose") != "ENGINEERING_TEST"
            or not journal.has_order_send_attempt(client_order_id)
        ):
            raise PaperOrderHttpError("paper order lookup identity is invalid")
        reference = self.verify_account(
            journal, expected_reference=str(intent["paper_account_ref"])
        )
        if intent.get("paper_account_ref") != reference:
            raise PaperOrderHttpError("paper order lookup identity is invalid")
        query = urlencode({"client_order_id": client_order_id})
        return self._request_and_record(
            journal,
            client_order_id,
            "GET",
            f"/v2/orders:by_client_order_id?{query}",
            None,
        )

    def cancel_engineering_entry_once(
        self, journal: Journal, client_order_id: str, started_at: datetime
    ) -> PaperHttpResponse:
        """Attempt one owned entry cancellation; HTTP acceptance is not finality."""
        journal.require_private_order_state()
        if journal.has_order_cancel_attempt(client_order_id):
            raise JournalError("order cancellation was already attempted")
        intent = journal.read_order_intent(client_order_id)
        if not isinstance(intent, dict) or set(intent) != {
            "paper_endpoint",
            "paper_account_ref",
            "purpose",
            "request_body",
        }:
            raise PaperOrderHttpError("reserved paper entry intent is invalid")
        if (
            intent["paper_endpoint"] != PAPER_TRADING_URL
            or not isinstance(intent["paper_account_ref"], str)
            or not intent["paper_account_ref"].strip()
            or (
                self._paper_account_ref is not None
                and intent["paper_account_ref"] != self._paper_account_ref
            )
            or intent["purpose"] != "ENGINEERING_TEST"
            or not journal.has_order_send_attempt(client_order_id)
        ):
            raise PaperOrderHttpError("reserved paper entry identity is invalid")
        request_body = intent["request_body"]
        if not isinstance(request_body, str):
            raise PaperOrderHttpError("reserved paper entry request is invalid")
        if not request_body or len(request_body.encode()) > self._max_request_bytes:
            raise PaperOrderHttpError("reserved paper entry request exceeds its cap")
        try:
            request = validate_saved_request(request_body)
            canonical = json.dumps(
                request, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (PaperContractError, ValueError, TypeError) as exc:
            raise PaperOrderHttpError(
                "reserved paper entry request is invalid"
            ) from exc
        if (
            canonical != request_body
            or request["client_order_id"] != client_order_id
            or request["side"] != "buy"
        ):
            raise PaperOrderHttpError(
                "only canonical owned buy entries may be canceled"
            )
        snapshot = self.reconcile_order_snapshot(journal, client_order_id)
        if snapshot is None or snapshot.status not in {
            "accepted",
            "new",
            "partially_filled",
        }:
            raise PaperOrderHttpError(
                "entry cancellation requires a working owned order"
            )
        journal.mark_order_cancel_attempt(
            client_order_id, snapshot.broker_order_id, started_at
        )
        return self._request_and_record(
            journal,
            client_order_id,
            "DELETE",
            f"/v2/orders/{snapshot.broker_order_id}",
            None,
        )

    def read_open_orders_page(
        self, *, after_order_id: str | None = None
    ) -> PaperHttpResponse:
        """Read one fixed-size open-order page with an exclusive UUID cursor."""
        query = [
            ("status", "open"),
            ("limit", "500"),
            ("nested", "false"),
            ("direction", "asc"),
        ]
        if after_order_id is not None:
            try:
                cursor = str(UUID(after_order_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("open-order cursor must be an order UUID") from exc
            query.append(("after_order_id", cursor))
        return self._request("GET", "/v2/orders?" + urlencode(query), None)

    def read_positions(self) -> PaperHttpResponse:
        """Read account positions without altering order or position state."""
        return self._request("GET", "/v2/positions", None)

    def read_account(self) -> PaperHttpResponse:
        """Read raw account evidence; callers must verify its bound identity."""
        return self._request("GET", "/v2/account", None)

    def read_native_oco_order(self, broker_order_id: str) -> PaperHttpResponse:
        """Read nested native order evidence without adopting or changing it."""
        try:
            identity = str(UUID(broker_order_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("native order lookup requires a broker UUID") from exc
        return self._request("GET", f"/v2/orders/{identity}?nested=true", None)

    def read_native_oco_by_client_id(self, client_order_id: str) -> PaperHttpResponse:
        """Read raw lookup evidence; no send, ownership or retry proof is implied.

        A collector must separately authenticate its bound account and retain
        these bytes. This endpoint has no documented nested parameter; a known
        broker UUID can subsequently be read with ``read_native_oco_order``.
        """
        if (
            type(client_order_id) is not str
            or not client_order_id
            or client_order_id != client_order_id.strip()
            or len(client_order_id) > 128
        ):
            raise ValueError("native lookup requires an exact client order ID")
        query = urlencode({"client_order_id": client_order_id})
        return self._request("GET", f"/v2/orders:by_client_order_id?{query}", None)

    def read_fill_activities_page(
        self, broker_order_id: str, *, page_token: str | None = None
    ) -> PaperHttpResponse:
        """Read one FILL page scoped to an owned, validated broker order UUID."""
        try:
            identity = str(UUID(broker_order_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("fill activity order must be a broker UUID") from exc
        query = [
            ("activity_types", "FILL"),
            ("order_id", identity),
            ("direction", "asc"),
            ("page_size", "100"),
        ]
        if page_token is not None:
            if not isinstance(page_token, str) or not page_token.strip():
                raise ValueError("fill activity cursor must be nonempty")
            query.append(("page_token", page_token))
        return self._request("GET", "/v2/account/activities?" + urlencode(query), None)

    def reconcile_order_snapshot(
        self, journal: Journal, client_order_id: str
    ) -> OrderSnapshot | None:
        """Return only an identity-matched lookup snapshot; None remains unknown."""
        response = self.get_order_by_client_id(journal, client_order_id)
        if response.status != 200:
            return None
        intent = journal.read_order_intent(client_order_id)
        if not isinstance(intent, dict) or not isinstance(
            intent.get("request_body"), str
        ):
            return None
        try:
            snapshot = validate_order_snapshot(intent["request_body"], response.body)
        except PaperContractError:
            return None
        for previous in journal.read_order_http_observations(client_order_id):
            if previous.http_status not in (200, 201) or previous.body is None:
                continue
            try:
                prior = validate_order_snapshot(intent["request_body"], previous.body)
            except PaperContractError:
                continue
            if prior.broker_order_id != snapshot.broker_order_id:
                return None
        now = datetime.now(UTC)
        journal.append_next(
            source_id="ALPACA_PAPER",
            feed="paper",
            event_kind="ORDER_LOOKUP_IDENTITY_MATCHED",
            payload=json.dumps(
                {
                    "client_order_id": client_order_id,
                    "status": snapshot.status,
                    "broker_order_id": snapshot.broker_order_id,
                    "response_sha256": hashlib.sha256(response.body).hexdigest(),
                }
            ).encode(),
            system_first_seen_at=now,
            processed_at=now,
        )
        return snapshot

    def audit_readonly_state(self, journal: Journal) -> dict[str, object]:
        """Capture account-scoped snapshots, without clearing unresolved sends."""
        self.verify_account(journal)
        counts: dict[str, int] = {}
        for kind, path in (
            ("open_orders", "/v2/orders?status=open&limit=500&nested=false"),
            ("positions", "/v2/positions"),
        ):
            response = self._request("GET", path, None)
            if response.status != 200:
                raise PaperOrderHttpError("read-only paper state query failed")
            decoded = decode_broker_json(response.body)
            if not isinstance(decoded, list):
                raise PaperOrderHttpError("read-only paper state shape is invalid")
            counts[kind] = len(decoded)
            now = datetime.now(UTC)
            journal.append_next(
                source_id="ALPACA_PAPER",
                feed="paper",
                event_kind="PAPER_" + kind.upper() + "_SNAPSHOT",
                payload=response.body,
                system_first_seen_at=now,
                processed_at=now,
            )
        self.verify_account(journal)
        return {
            "paper_account_verified": True,
            "open_orders_snapshot_empty": counts["open_orders"] == 0,
            "positions_snapshot_empty": counts["positions"] == 0,
            "order_list_cap_reached": counts["open_orders"] >= 500,
            "local_send_attempts_present": any(
                journal.has_order_send_attempt(cid)
                for cid in journal.read_order_intent_ids()
            ),
            "snapshots_atomic": False,
            "trading_authorized": False,
        }

    def _request_and_record(
        self,
        journal: Journal,
        client_order_id: str,
        method: str,
        path: str,
        body: bytes | None,
    ) -> PaperHttpResponse:
        try:
            response = self._request(method, path, body)
        except PaperOrderHttpError:
            journal.record_order_http_observation(
                client_order_id,
                OrderHttpObservation(method, "TRANSPORT_UNKNOWN", datetime.now(UTC)),
            )
            raise
        journal.record_order_http_observation(
            client_order_id,
            OrderHttpObservation(
                method,
                "HTTP_RESPONSE",
                datetime.now(UTC),
                response.status,
                response.body,
            ),
        )
        return response

    def _request(self, method: str, path: str, body: bytes | None) -> PaperHttpResponse:
        headers = self._credentials.auth_headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._exchange(
                method,
                path,
                headers,
                body,
                self._timeout,
                self._max_response_bytes,
            )
        except (HTTPException, TimeoutError, OSError) as exc:
            raise PaperOrderHttpError("paper order request outcome is unknown") from exc
        if not isinstance(response, PaperHttpResponse):
            raise PaperOrderHttpError("paper order response is malformed")
        if type(response.status) is not int or not 100 <= response.status <= 599:
            raise PaperOrderHttpError("paper order response status is malformed")
        if not isinstance(response.body, bytes):
            raise PaperOrderHttpError("paper order response body is malformed")
        if len(response.body) > self._max_response_bytes:
            raise PaperOrderHttpError("paper order response exceeds its cap")
        return response


def _exchange_paper_https(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
    max_response_bytes: int,
) -> PaperHttpResponse:
    """Send exactly one HTTPS request to the hard-coded paper host."""
    deadline = time.monotonic() + timeout_seconds
    connection = HTTPSConnection(_PAPER_HOST, timeout=timeout_seconds)
    try:
        connection.connect()
        _set_remaining_timeout(connection, deadline)
        connection.request(method, path, body=body, headers=dict(headers))
        _set_remaining_timeout(connection, deadline)
        response = connection.getresponse()
        content_length = response.getheader("Content-Length")
        if content_length is not None and int(content_length) > max_response_bytes:
            raise PaperOrderHttpError("paper order response exceeds its cap")
        received = bytearray()
        while True:
            _set_remaining_timeout(connection, deadline)
            chunk = response.read1(min(65_536, max_response_bytes + 1 - len(received)))
            if not chunk:
                return PaperHttpResponse(response.status, bytes(received))
            received.extend(chunk)
            if len(received) > max_response_bytes:
                raise PaperOrderHttpError("paper order response exceeds its cap")
    except (HTTPException, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, PaperOrderHttpError):
            raise
        raise PaperOrderHttpError("paper order request outcome is unknown") from exc
    finally:
        connection.close()


def _set_remaining_timeout(connection: HTTPSConnection, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or connection.sock is None:
        raise PaperOrderHttpError("paper order request outcome is unknown")
    connection.sock.settimeout(remaining)
