"""Canonical immutable source selection shared by risk owners and incidents."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class IncidentRiskRequest:
    capture_sequence: int
    calendar_sequence: int
    max_age: timedelta
    max_metadata_age: timedelta
    mark_refs: tuple[tuple[str, int, int, int], ...] = ()

    def to_dict(self) -> dict[str, object]:
        for value in (self.capture_sequence, self.calendar_sequence):
            if type(value) is not int or value <= 0:
                raise ValueError("positive source sequences required")
        for age in (self.max_age, self.max_metadata_age):
            if type(age) is not timedelta or age <= timedelta(0):
                raise ValueError("positive source age limits required")
        if type(self.mark_refs) is not tuple or any(
            type(row) is not tuple
            or len(row) != 4
            or type(row[0]) is not str
            or not row[0].strip()
            or any(type(v) is not int or v < 0 for v in row[1:])
            or row[1] == 0
            or row[3] == 0
            for row in self.mark_refs
        ):
            raise ValueError("immutable mark identities required")
        return {
            "capture_sequence": self.capture_sequence,
            "calendar_sequence": self.calendar_sequence,
            "max_age_us": self.max_age // timedelta(microseconds=1),
            "max_metadata_age_us": self.max_metadata_age // timedelta(microseconds=1),
            "mark_refs": [list(row) for row in self.mark_refs],
        }

    @classmethod
    def from_dict(cls, raw: object) -> "IncidentRiskRequest":
        if not isinstance(raw, dict) or set(raw) != {
            "capture_sequence",
            "calendar_sequence",
            "max_age_us",
            "max_metadata_age_us",
            "mark_refs",
        }:
            raise ValueError("invalid bound risk request")
        if any(
            type(raw[key]) is not int
            for key in (
                "capture_sequence",
                "calendar_sequence",
                "max_age_us",
                "max_metadata_age_us",
            )
        ) or not isinstance(raw["mark_refs"], list):
            raise ValueError("invalid bound risk request values")
        rows: list[tuple[str, int, int, int]] = []
        for row in raw["mark_refs"]:
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError("invalid mark request")
            rows.append((row[0], row[1], row[2], row[3]))
        request = cls(
            raw["capture_sequence"],
            raw["calendar_sequence"],
            timedelta(microseconds=raw["max_age_us"]),
            timedelta(microseconds=raw["max_metadata_age_us"]),
            tuple(rows),
        )
        if request.to_dict() != raw:
            raise ValueError("noncanonical bound risk request")
        return request
