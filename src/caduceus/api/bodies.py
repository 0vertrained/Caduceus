"""The API's request bodies. Free text is bounded here so a request cannot park megabytes in a case; the workflow
gives the friendly limits."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SwitchBody(BaseModel):
    on: bool


class ReturnBody(BaseModel):
    reason: str = Field(max_length=2_000)


class AttestBody(BaseModel):
    answers: dict[str, str]


class DraftBody(BaseModel):
    text: str = Field(max_length=20_000)


class NoteBody(BaseModel):
    text: str = Field(max_length=20_000)


class AppealBody(BaseModel):
    text: str = Field(max_length=20_000)


class UrgencyBody(BaseModel):
    expedited: bool
    reason: str = Field(default="", max_length=500)


class ConfirmFaxBody(BaseModel):
    values: dict[str, Annotated[str, Field(max_length=300)]] = Field(max_length=20)
    # The fields sent to a person that they ticked as matching the fax: the case is refused until each is ticked or
    # corrected.
    checked: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)


class AskClinicBody(BaseModel):
    missing: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)
    resend: bool = False
    referral: str | None = Field(default=None, max_length=9)  # the number as the person has it, to name in the request


class MatchBody(BaseModel):
    """A patient lookup. The name travels in the body, never the URL, so it stays out of access logs."""
    name: str = Field(max_length=120)
    birth_date: str = Field(max_length=10)
    ref: str | None = Field(default=None, max_length=32)


class LaunchBody(BaseModel):
    """What the EHR put on Caduceus's launch address, sent on in the body so it stays out of access logs."""
    iss: str = Field(min_length=1, max_length=512)
    launch: str = Field(min_length=1, max_length=2048)
    redirect_uri: str = Field(min_length=1, max_length=512)


class CallbackBody(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=64)


class EhrCaseBody(BaseModel):
    service: str = Field(max_length=10)
    side: Literal["left", "right"] | None = None
    payer: str = Field(max_length=100)
