"""Account time-zone input shared by automatic detection and settings."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator


class UserTimezoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    only_if_unset: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "Choose a valid IANA time zone, such as America/Guyana"
            ) from exc
        return value
