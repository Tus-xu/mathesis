import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.models.database import async_session_factory
async def main():
    async with async_session_factory() as s:
        for name, q in [
          ("question_bank_total", "SELECT count(*) FROM question_bank WHERE deleted_at IS NULL"),
          ("by_batch_diff", "SELECT coalesce(source_batch,'NULL'), difficulty, count(*) FROM question_bank GROUP BY 1,2 ORDER BY 1,3 DESC"),
          ("image_filled", "SELECT count(*) FROM question_bank WHERE jsonb_array_length(image) > 0"),
          ("knowledge_points", "SELECT count(*) FROM knowledge_points"),
          ("ai_quality_scores", "SELECT scene, count(*), round(avg(total_score)::numeric,1) FROM ai_quality_scores GROUP BY 1 ORDER BY 1"),
          ("alembic", "SELECT version_num FROM alembic_version"),
        ]:
            r = await s.execute(text(q))
            print(name, "=", r.all() if name != "alembic" else r.scalar())
asyncio.run(main())