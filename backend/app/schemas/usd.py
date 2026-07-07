from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class USDBase(BaseModel):
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class USDCreate(USDBase):
    pass


class USDUpdate(BaseModel):
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None


class USDRead(USDBase):
    class Config:
        from_attributes = True