from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

LocaleField = Literal["el", "en"]
RoleField = Literal["employee", "manager", "hr_admin", "company_admin"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PageOut[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
