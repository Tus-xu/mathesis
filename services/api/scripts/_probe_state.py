import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT scene, count(*), round(avg(total_score)::numeric,1) FROM ai_quality_scores GROUP BY 1 ORDER BY 1"
            )
        )
        print("SCORES =", r.all())


asyncio.run(main())
