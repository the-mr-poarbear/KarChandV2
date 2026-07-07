from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.usd import sync_usd_history

router = APIRouter(prefix="/usd", tags=["USD"])


@router.post("/sync")
def sync(
    start: int = 0,
    db: Session = Depends(get_db),
):
    return sync_usd_history(db, start=start)