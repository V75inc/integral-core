"""Native workspace email configuration and transport contracts."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Provider = Literal["resend", "sendgrid", "smtp"]


class EmailConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider = "resend"
    from_email: str = Field(default="", max_length=320)
    from_name: str = Field(default="", max_length=200)
    reply_to: str = Field(default="", max_length=320)
    api_key: Optional[str] = Field(default=None, max_length=4096, repr=False)
    smtp_host: str = Field(default="", max_length=253)
    smtp_port: Literal[465, 587] = 587
    smtp_username: str = Field(default="", max_length=320)
    smtp_password: Optional[str] = Field(default=None, max_length=4096, repr=False)
    smtp_tls: Literal["starttls", "implicit"] = "starttls"
    webhook_key: Optional[str] = Field(default=None, max_length=4096, repr=False)

    @field_validator("from_email", "reply_to")
    @classmethod
    def address(cls, value: str) -> str:
        value = value.strip()
        if value and (
            value.count("@") != 1
            or any(c.isspace() or c in "<>,;()\r\n\x00" for c in value)
        ):
            raise ValueError("Enter a single email address")
        return value

    @field_validator("from_name", "smtp_host", "smtp_username")
    @classmethod
    def header(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Line breaks are not allowed")
        return value.strip()


class SaveEmailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    configuration: EmailConfiguration


class EmailRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class TestEmailDraft(EmailRevision):
    to: Optional[str] = Field(default=None, max_length=320)

    @field_validator("to")
    @classmethod
    def recipient(cls, value: Optional[str]) -> Optional[str]:
        return EmailConfiguration.address(value) if value is not None else None


class EmailEnqueueResult(BaseModel):
    message_id: str
    status: Literal["queued"] = "queued"
