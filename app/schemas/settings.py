from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SettingUpsert(BaseModel):
    key: str
    value: str


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    updated_at: datetime
