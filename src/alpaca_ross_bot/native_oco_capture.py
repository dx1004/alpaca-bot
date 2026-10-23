"""Finite GET-only native OCO observations in the existing private journal.

Raw failures remain evidence. Neither finding an order nor collecting its fills
establishes local ownership, submission authority or continuous protection.
"""

import base64
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from urllib.parse import urlencode
from uuid import UUID

from .journal import Journal, JournalError
from .native_oco_evidence import native_oco_order_ids
from .paper_order_contract import decode_broker_json, paper_account_ref
from .paper_order_http import PaperHttpResponse, PaperOrderHttp


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise JournalError("UTC native OCO capture clock required")
    return value


def capture_native_oco_group(
    api: PaperOrderHttp,
    journal: Journal,
    *,
    account_ref: str,
    broker_order_id: str,
    clock: Callable[[], datetime],
    max_fill_pages: int,
) -> int:
    """Retain raw GETs, including bounded failures; return only their sequence.

    This cannot bind a new account, allocate a group, create/send/cancel an
    intent, amend an order, or attest a complete replacement history.
    """
    journal.require_private_order_state()
    if type(api) is not PaperOrderHttp:
        raise JournalError("canonical paper transport required for origin capture")
    if (
        type(account_ref) is not str
        or not account_ref
        or journal.read_paper_account_ref() != account_ref
    ):
        raise JournalError("native OCO capture requires the existing bound account")
    if type(max_fill_pages) is not int or max_fill_pages < 1:
        raise ValueError("positive native OCO fill-page budget required")
    try:
        order_id = str(UUID(broker_order_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("native OCO capture requires a broker UUID") from exc
    started = _utc(clock())
    previous = started
    requests: list[dict[str, object]] = []

    def read(
        path: str, call: Callable[[], PaperHttpResponse]
    ) -> PaperHttpResponse | None:
        nonlocal previous
        began = _utc(clock())
        if began < previous:
            raise JournalError("native OCO capture clock rewound")
        response = None
        error = None
        try:
            response = call()
        except Exception as exc:
            # Do not retain exception messages or request headers: either can
            # expose authentication data. Transport failures cannot imply 404.
            error = type(exc).__name__
        completed = _utc(clock())
        if completed < began:
            raise JournalError("native OCO capture clock rewound")
        requests.append(
            {
                "method": "GET",
                "path": path,
                "status": None if response is None else response.status,
                "body": None
                if response is None
                else base64.b64encode(response.body).decode(),
                "error": error,
                "started_at": began.isoformat(),
                "completed_at": completed.isoformat(),
            }
        )
        previous = completed
        return response

    account = read("/v2/account", api.read_account)
    authenticated = False
    if account is not None and account.status == 200:
        try:
            authenticated = paper_account_ref(account.body) == account_ref
        except ValueError:
            pass
    if authenticated:
        order = read(
            f"/v2/orders/{order_id}?nested=true",
            lambda: api.read_native_oco_order(order_id),
        )
        ids: tuple[str, ...] = ()
        if order is not None and order.status == 200:
            try:
                ids = native_oco_order_ids(order.body, order_id)
            except ValueError:
                pass
        for identity in ids:
            cursor = None
            seen: set[str] = set()
            for _ in range(max_fill_pages):
                query = [
                    ("activity_types", "FILL"),
                    ("order_id", identity),
                    ("direction", "asc"),
                    ("page_size", "100"),
                ]
                if cursor is not None:
                    query.append(("page_token", cursor))
                page = read(
                    "/v2/account/activities?" + urlencode(query),
                    partial(api.read_fill_activities_page, identity, page_token=cursor),
                )
                if page is None or page.status != 200:
                    break
                try:
                    rows = decode_broker_json(page.body)
                except ValueError:
                    break
                if not isinstance(rows, list) or not rows or len(rows) > 100:
                    break
                last = rows[-1]
                next_cursor = last.get("id") if isinstance(last, dict) else None
                if (
                    type(next_cursor) is not str
                    or not next_cursor
                    or next_cursor != next_cursor.strip()
                    or next_cursor in seen
                ):
                    break
                seen.add(next_cursor)
                cursor = next_cursor
    completed = _utc(clock())
    if completed < previous:
        raise JournalError("native OCO capture clock rewound")
    payload = json.dumps(
        {
            "schema": 2,
            "transport_origin": api.native_http_origin,
            "paper_endpoint": "https://paper-api.alpaca.markets",
            "account_ref": account_ref,
            "requested_order_id": order_id,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "requests": requests,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return journal.append_next(
        source_id="ALPACA_NATIVE_OCO",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=payload,
        system_first_seen_at=started,
        processed_at=completed,
    ).sequence
