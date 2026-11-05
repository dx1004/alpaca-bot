"""Origin-checked SOURCE HTTP GET captures; never fill ownership or release.

Only the canonical injected PaperOrderHttp transport is supported. This module
creates no client, credentials, order intent or POST/retry path.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from .crossed_stop_capture import (
    AUTHORITY as AUTHORITY,
)
from .crossed_stop_capture import (
    SourceCloseGetResponse,
    _CaptureAttemptReader,
    _SourceCloseCaptureCore,
    _walk,
)
from .journal import Journal, JournalError
from .native_oco_requests import _utc
from .oco_research import OcoResearchState
from .paper_order_http import PaperOrderHttp
from .source_close_http import ORIGIN as ORIGIN
from .source_close_http import SourceCloseHttp, read_source_close_http_at

SOURCE = "ALPACA_SOURCE_CLOSE_HTTP_GET"
KIND = "SOURCE_CLOSE_HTTP_RAW_CAPTURE"


class _HttpCaptureCore(_SourceCloseCaptureCore):
    _source = SOURCE
    _kind = KIND
    _origin = ORIGIN
    _attempt_origin = ORIGIN

    def _walk(
        self, pin: dict[str, Any], read: Callable[[str], dict[str, Any]]
    ) -> dict[str, Any]:
        return _walk(pin, read, http_source=True)


class _HttpAttemptReader:
    def __init__(self, journal: Journal, initial: OcoResearchState) -> None:
        self.journal, self.initial = journal, initial

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> list[dict[str, Any]]:
        return read_source_close_http_at(
            self.journal,
            self.initial,
            through_sequence=through_sequence,
            decision_at=decision_at,
        )


class SourceCloseHttpCaptures:
    """Compose the strict raw collector without exposing its callback interface."""

    def __init__(self, owner: SourceCloseHttp) -> None:
        if type(owner) is not SourceCloseHttp:
            raise JournalError("canonical source HTTP owner required")
        self.owner, self.initial, self.journal = owner, owner.initial, owner.journal
        self._core = _HttpCaptureCore(owner)

    def capture(
        self,
        *,
        attempt_ref: dict[str, Any],
        decision_at: datetime,
        api: PaperOrderHttp,
        clock: Callable[[], datetime],
        max_body_bytes: int,
        max_fill_pages: int,
        page_size: int,
    ) -> dict[str, Any]:
        if type(api) is not PaperOrderHttp:
            raise JournalError("canonical source HTTP GET transport required")
        decision = _utc(decision_at)
        horizon = self.journal.read_evidence_sequence()
        rows = [
            row
            for row in self.owner.read_verified_at(horizon, decision)
            if row["attempt_ref"] == attempt_ref
        ]
        if len(rows) != 1:
            raise JournalError("verified source HTTP attempt reference required")
        pre = rows[0]["precall"]

        def guard() -> None:
            if (
                pre["origin"] != ORIGIN
                or ORIGIN != "INJECTED_EXCHANGE"
                or api.native_http_origin != pre["origin"]
                or (
                    api._paper_account_ref is not None
                    and api._paper_account_ref != pre["account_ref"]
                )
            ):
                raise JournalError(
                    "source HTTP GET origin or configured account differs"
                )

        guard()

        def read(path: str) -> SourceCloseGetResponse:
            response = api._request("GET", path, None)
            return SourceCloseGetResponse(response.status, response.body)

        return self._core._capture(
            attempt_ref=attempt_ref,
            decision_at=decision,
            get=read,
            clock=clock,
            max_body_bytes=max_body_bytes,
            max_fill_pages=max_fill_pages,
            page_size=page_size,
            before_read=guard,
        )

    def view(self) -> list[dict[str, Any]]:
        return self._core.view()

    def read_verified_at(
        self, horizon: int, decision_at: datetime
    ) -> list[dict[str, Any]]:
        return self._core.read_verified_at(horizon, decision_at)


def read_source_close_http_captures_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    through_sequence: int,
    decision_at: datetime,
) -> list[dict[str, Any]]:
    """Read the bounded HTTP capture prefix without constructing a sender."""
    journal.require_private_order_state()
    if journal.read_paper_account_ref() != initial.account_ref:
        raise JournalError("source HTTP capture reader account differs")
    reader: _CaptureAttemptReader = _HttpAttemptReader(journal, initial)
    return _HttpCaptureCore(reader).read_verified_at(through_sequence, decision_at)
