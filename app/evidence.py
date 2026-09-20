"""Human observations are separate from the model's adoption claims."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    criterion: str = Field(min_length=1, max_length=500)
    observation: str = Field(min_length=1, max_length=2000)
    status: Literal["passed", "failed", "unverified"]
    baseline_run_id: str | None = Field(default=None, min_length=1, max_length=100)
