import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT id, stem, q_type, year, source FROM question_bank WHERE source_batch IN ('gkb-2010-2022','gkb-2023','gkb-2024') ORDER BY random() LIMIT 12"
            )
        )
        for row in r.all():
            stem = row.stem[:70].replace("\n", " ")
            print("YR", row.year, "|", row.q_type, "|", row.source, "|", stem)
        r2 = await s.execute(
            text(
                "SELECT year, q_type, count(*) FROM question_bank WHERE source_batch IN ('gkb-2010-2022','gkb-2023','gkb-2024') GROUP BY 1,2 ORDER BY 1,2"
            )
        )
        print("BY_YEAR_TYPE =", r2.all())
        r3 = await s.execute(
            text(
                "SELECT id, stem FROM question_bank WHERE source_batch='tal-scq5k' ORDER BY random() LIMIT 4"
            )
        )
        for row in r3.all():
            print("TAL |", row.stem[:90].replace("\n", " "))


asyncio.run(main())
