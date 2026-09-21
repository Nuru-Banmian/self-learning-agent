"""Legacy scheduling payloads and schema retained as read-only history."""

import sqlite3
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class SchedulingRemoved(Exception):
    code = "roadmap_scheduling_removed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "路线排期已取消，请按自己的节奏推进。"
            "如需安排某条待办，请在普通待办中手动编辑日期。"
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS roadmap_dates (
    node_id TEXT PRIMARY KEY REFERENCES roadmap_nodes, scheduled_date TEXT);
CREATE TABLE IF NOT EXISTS schedule_proposals (
    id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs, status TEXT NOT NULL,
    content TEXT NOT NULL);
"""


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    start_text: str | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=lambda: list(range(7)), min_length=1, max_length=7
    )
    deadline_text: str | None = None
    clear: bool = False


class ScheduleConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


def preview(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    raise SchedulingRemoved()


def confirm(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    raise SchedulingRemoved()
