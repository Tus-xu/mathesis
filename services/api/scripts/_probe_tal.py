import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT difficulty, count(*) FROM question_bank WHERE source_batch='tal-scq5k' GROUP BY 1 ORDER BY 1"
            )
        )
        print("TAL =", r.all())


asyncio.run(main())
