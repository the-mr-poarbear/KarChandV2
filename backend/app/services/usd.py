import requests

from datetime import datetime
from sqlalchemy.orm import Session

from app.crud import usd as crud_usd
from app.schemas.usd import USDCreate

URL = (
    "https://api.tgju.org/v1/market/indicator/summary-table-data/"
    "price_dollar_rl"
)


def _parse_row(row: list[str]) -> USDCreate:
    return USDCreate(
        date=datetime.strptime(row[6], "%Y/%m/%d").date(),
        open=int(row[0].replace(",", "")),
        high=int(row[1].replace(",", "")),
        low=int(row[2].replace(",", "")),
        close=int(row[3].replace(",", "")),
    )


def sync_usd_history(
    db: Session,
    start: int = 0,
    page_size: int = 1000,
) -> dict:

    params = {
        "lang": "fa",
        "order_dir": "asc",
        "columns[0][data]": 0,
        "columns[1][data]": 1,
        "columns[2][data]": 2,
        "start": start,
        "length": page_size,
        "convert_to_ad": 1,
        "_": int(datetime.now().timestamp() * 1000),
    }

    response = requests.get(URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()["data"]

    inserted = 0

    for row in data:
        usd = _parse_row(row)
        crud_usd.upsert(db, usd)
        inserted += 1

    return {
        "count": inserted,
        "start": start,
    }