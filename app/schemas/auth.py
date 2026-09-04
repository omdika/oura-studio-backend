from datetime import datetime
from pydantic import BaseModel, EmailStr


class GoogleAuthRequest(BaseModel):
    id_token: str
    invitation_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    expires_at: datetime


class VerifyInviteRequest(BaseModel):
    email: EmailStr
    code: str


class VerifyInviteResponse(BaseModel):
    invitation_token: str


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role: str = "member"


class InvitationResponse(BaseModel):
    email: EmailStr
    code: str
    role: str
    expires_at: datetime
