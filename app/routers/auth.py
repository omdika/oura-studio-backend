from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.owner import OwnerAccount
from app.schemas.auth import GoogleAuthRequest, TokenResponse
from app.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse)
def google_sign_in(body: GoogleAuthRequest, db: Session = Depends(get_db)):
    if not body.id_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="id_token is required")

    try:
        # audience=settings.google_client_id makes this verify the 'aud' claim too (handoff step 1+2).
        payload = google_id_token.verify_oauth2_token(
            body.id_token, google_requests.Request(), settings.google_client_id
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token")

    google_sub = payload["sub"]
    email = payload["email"]

    if email != settings.authorized_owner_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not authorized")

    # Single-owner app: at most one owner_account row ever exists (handoff Section 3).
    owner = db.query(OwnerAccount).first()
    if owner is None:
        owner = OwnerAccount(google_sub=google_sub, email=email)
        db.add(owner)
        db.commit()
        db.refresh(owner)
    elif owner.google_sub != google_sub:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google account mismatch")

    token, expires_at = create_access_token(owner.id)
    return TokenResponse(access_token=token, expires_at=expires_at)
