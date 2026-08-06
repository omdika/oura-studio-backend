from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_owner
from app.models.settings import Setting
from app.schemas.settings import SettingOut, SettingUpsert

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_owner)])


@router.get("", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)):
    return db.query(Setting).order_by(Setting.key).all()


@router.patch("", response_model=SettingOut)
def upsert_setting(body: SettingUpsert, db: Session = Depends(get_db)):
    setting = db.get(Setting, body.key)
    if setting is None:
        setting = Setting(key=body.key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    db.commit()
    db.refresh(setting)
    return setting
