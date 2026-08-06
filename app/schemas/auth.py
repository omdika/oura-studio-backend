from datetime import datetime

from pydantic import BaseModel


class GoogleAuthRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    expires_at: datetime
