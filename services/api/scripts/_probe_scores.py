import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT scene, kp_code, expected_difficulty, output_difficulty, q_type, b_hit, c_similarity, c_deduction, total_score, left(question_text,40), note FROM ai_quality_scores ORDER BY created_at DESC LIMIT 5"
            )
        )
        for row in r.all():
            print(dict(row._mapping))


asyncio.run(main())
