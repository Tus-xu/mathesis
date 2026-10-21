import uuid

from app.kernel.rag import RAGPipeline
from app.models.chunk import Chunk
from app.models.database import async_session_factory
from app.models.knowledge_doc import KnowledgeDoc


async def test_trgm_retrieves_student_textbook_when_scope_filter_is_used():
    """数组参数转换错误会使已导入教材在 student 范围内完全无法关键词召回。

    迭代20 门禁修复（可复现性）：本测试用真实教材语料词「集合的概念」检索并断言
    **恰好 1 行**——全量套件下此前测试文件遗留的教材切片同样命中该词，导致
    `len(rows)==1` 顺序性误报。改为在内容里埋一个全库唯一的随机 token、用该
    token 检索：唯一性保证「恰好 1 行」只可能命中本用例数据；若 student 范围
    的数组参数回归复发，本用例数据将召回 0 行，断言照样红——守卫强度不降。
    """
    # 全库唯一 token：既有切片不可能包含，隔离其他测试的遗留数据
    token = f" TrgmToken{uuid.uuid4().hex[:12]}X 集合的概念"
    async with async_session_factory() as db:
        doc = KnowledgeDoc(
            title="数学必修第一册（人教A版2019）",
            source_type="textbook",
            status="ready",
            meta_={"scope": "student", "corpus_class": "student_textbook"},
        )
        db.add(doc)
        await db.flush()
        db.add(
            Chunk(
                doc_id=doc.id,
                content=f"集合的概念：{token} 集合是由一些确定的、不同的对象组成的整体。",
                kp_ids=[],
                meta_={"section": "1.1 集合的概念"},
                chunk_index=0,
            )
        )
        await db.commit()

        rows = await RAGPipeline()._trgm_search(
            token.strip(),
            db,
            content_type="textbook",
            scope="student",
        )

    assert len(rows) == 1
    assert rows[0].doc_title == "数学必修第一册（人教A版2019）"
    assert "集合的概念" in rows[0].content
