"""Single-request Alpaca paper order transport; no retry or order policy."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from math import isfinite
from urllib.parse import urlencode

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


class PaperOrderHttpError(ValueError):
    """A paper request failed without establishing its broker outcome."""


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
