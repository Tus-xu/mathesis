"""数据库会话管理

提供 async session 和依赖注入。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    # 连接取消/对端关闭会产生池化"尸体连接"，checkout 时轻量探测回收
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 后台任务专用会话工厂（NullPool）：BackgroundTasks/create_task 触发的异步任务
# 不复用请求连接池，避免跨 event loop 时池化连接的协议失效问题（迭代05）
_background_engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
background_session_factory = async_sessionmaker(
    _background_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：提供数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """初始化数据库（开发用，生产用 Alembic）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
