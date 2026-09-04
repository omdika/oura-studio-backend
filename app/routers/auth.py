import string
import random
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.owner import OwnerAccount, Invitation
from app.schemas.auth import (
    GoogleAuthRequest,
    TokenResponse,
    VerifyInviteRequest,
    VerifyInviteResponse,
    InvitationCreateRequest,
    InvitationResponse
)
from app.security import (
    create_access_token,
    create_invitation_token,
    decode_invitation_token
)
from app.deps import get_current_owner, get_current_admin

router = APIRouter(prefix="/auth", tags=["auth"])
router_invitations = APIRouter(prefix="/invitations", tags=["invitations"])


def generate_invitation_code() -> str:
    """Generate a random 6-character uppercase alphanumeric code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


@router_invitations.post("", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_invitation(
    body: InvitationCreateRequest,
    db: Session = Depends(get_db),
    admin: OwnerAccount = Depends(get_current_admin)
):
    if body.email.lower() in settings.authorized_owner_emails:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email admin tidak perlu diundang"
        )

    now = datetime.now(timezone.utc)
    existing = db.query(Invitation).filter(
        Invitation.email == body.email.lower()
    ).first()
    
    if existing:
        if not existing.is_used and existing.expires_at > now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Undangan aktif untuk email ini sudah ada"
            )
        else:
            db.delete(existing)
            db.commit()

    code = generate_invitation_code()
    while db.query(Invitation).filter(Invitation.code == code).first() is not None:
        code = generate_invitation_code()

    expires_at = now + timedelta(hours=48)
    invitation = Invitation(
        email=body.email.lower(),
        code=code,
        role=body.role,
        created_by=admin.id,
        expires_at=expires_at
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    return InvitationResponse(
        email=invitation.email,
        code=invitation.code,
        role=invitation.role,
        expires_at=invitation.expires_at
    )


@router.post("/verify-invite", response_model=VerifyInviteResponse)
def verify_invite(body: VerifyInviteRequest, db: Session = Depends(get_db)):
    code_upper = body.code.strip().upper()
    invitation = db.query(Invitation).filter(
        Invitation.email == body.email.lower(),
        Invitation.code == code_upper
    ).first()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kode undangan tidak valid"
        )

    if invitation.is_used:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Kode undangan sudah pernah digunakan"
        )

    now = datetime.now(timezone.utc)
    if invitation.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Kode undangan telah kedaluwarsa"
        )

    temp_token = create_invitation_token(invitation.email)
    return VerifyInviteResponse(invitation_token=temp_token)


@router.post("/google", response_model=TokenResponse)
def google_sign_in(body: GoogleAuthRequest, db: Session = Depends(get_db)):
    if not body.id_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="id_token is required")

    try:
        payload = google_id_token.verify_oauth2_token(
            body.id_token, google_requests.Request(), settings.google_client_id
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token")

    google_sub = payload["sub"]
    email = payload["email"].lower()

    now = datetime.now(timezone.utc)

    if email in settings.authorized_owner_emails:
        owner = db.query(OwnerAccount).filter(OwnerAccount.email == email).first()
        if owner is None:
            owner = OwnerAccount(google_sub=google_sub, email=email, role="admin")
            db.add(owner)
            db.commit()
            db.refresh(owner)
        else:
            if owner.role != "admin":
                owner.role = "admin"
                db.commit()
                db.refresh(owner)
            if owner.google_sub != google_sub:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google account mismatch")
        
        token, expires_at = create_access_token(owner.id)
        return TokenResponse(access_token=token, expires_at=expires_at)

    owner = db.query(OwnerAccount).filter(OwnerAccount.email == email).first()
    if owner is not None:
        if owner.google_sub != google_sub:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google account mismatch")
        token, expires_at = create_access_token(owner.id)
        return TokenResponse(access_token=token, expires_at=expires_at)

    if not body.invitation_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrasi anggota memerlukan token undangan"
        )

    invited_email = decode_invitation_token(body.invitation_token)
    if not invited_email or invited_email.lower() != email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token undangan tidak valid atau tidak sesuai dengan email Google Anda"
        )

    invitation = db.query(Invitation).filter(
        Invitation.email == email,
        Invitation.is_used == False,
        Invitation.expires_at > now
    ).first()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Undangan tidak valid, sudah digunakan, atau kedaluwarsa"
        )

    owner = OwnerAccount(google_sub=google_sub, email=email, role=invitation.role)
    db.add(owner)
    
    invitation.is_used = True
    
    db.commit()
    db.refresh(owner)

    token, expires_at = create_access_token(owner.id)
    return TokenResponse(access_token=token, expires_at=expires_at)
