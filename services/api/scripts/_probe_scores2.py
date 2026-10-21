import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text("SELECT count(*), avg(total_score), min(total_score) FROM ai_quality_scores")
        )
        print("总数/平均分/最低分 =", r.one())
        r = await s.execute(
            text(
                "SELECT scene, expected_difficulty, output_difficulty, b_hit, c_similarity, c_deduction, total_score FROM ai_quality_scores ORDER BY created_at"
            )
        )
        for row in r.all():
            print(" -", dict(row._mapping))


asyncio.run(main())
