import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.models.database import async_session_factory
async def main():
    async with async_session_factory() as s:
        for name, q in [
          ("image_filled", "SELECT count(*) FROM question_bank WHERE jsonb_array_length(image) > 0"),
          ("by_source", "SELECT coalesce(source,'NULL'), count(*) FROM question_bank GROUP BY 1 ORDER BY 2 DESC LIMIT 12"),
          ("by_batch_diff", "SELECT coalesce(source_batch,'NULL'), difficulty, count(*) FROM question_bank GROUP BY 1,2 ORDER BY 3 DESC LIMIT 24"),
          ("by_type", "SELECT q_type, count(*) FROM question_bank GROUP BY 1"),
          ("img_sample", "SELECT id, left(stem,60), image FROM question_bank WHERE jsonb_array_length(image) > 0 LIMIT 4"),
        ]:
            try:
                r = await s.execute(text(q))
                print(name, "=", r.all() if name != "image_filled" else r.scalar())
            except Exception as e:
                print(name, "ERR", str(e)[:120])
asyncio.run(main())