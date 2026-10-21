"""SQLAlchemy 模型测试

测试所有模型可以正确实例化，验证表结构。
"""

import uuid

from app.models.ai_call import AICall
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.role_binding import RoleBinding
from app.models.user import User


class TestModelDefinitions:
    """测试所有 SQLAlchemy 模型定义"""

    def test_all_models_registered_on_base(self):
        """所有 15 张表注册在 Base.metadata"""
        table_names = set(Base.metadata.tables.keys())
        expected = {
            "users",
            "role_bindings",
            "classes",
            "class_members",
            "conversations",
            "messages",
            "user_profiles",
            "episodic_memories",
            "knowledge_points",
            "knowledge_docs",
            "chunks",
            "skills",
            "skill_runs",
            "ai_calls",
            "events",
        }
        assert expected.issubset(table_names), f"缺少表: {expected - table_names}"

    def test_table_count(self):
        """至少有 15 张表"""
        assert len(Base.metadata.tables) >= 15

    def test_user_model_fields(self):
        """User 模型字段正确"""
        columns = {c.name for c in User.__table__.columns}
        assert "id" in columns
        assert "phone" in columns
        assert "email" in columns
        assert "nickname" in columns
        assert "avatar_url" in columns
        assert "status" in columns
        assert "created_at" in columns
        assert "updated_at" in columns
        assert "deleted_at" in columns

    def test_conversation_model_fields(self):
        """Conversation 模型字段正确"""
        columns = {c.name for c in Conversation.__table__.columns}
        assert "id" in columns
        assert "user_id" in columns
        assert "active_role" in columns
        assert "title" in columns
        assert "message_count" in columns

    def test_message_model_fields(self):
        """Message 模型字段正确"""
        columns = {c.name for c in Message.__table__.columns}
        assert "id" in columns
        assert "conversation_id" in columns
        assert "client_msg_id" in columns
        assert "role" in columns
        assert "content" in columns
        assert "envelope" in columns

    def test_role_binding_model_fields(self):
        """RoleBinding 模型字段正确"""
        columns = {c.name for c in RoleBinding.__table__.columns}
        assert "id" in columns
        assert "user_id" in columns
        assert "role" in columns
        assert "verified" in columns
        assert "org_name" in columns

    def test_ai_call_model_fields(self):
        """AICall 模型字段正确"""
        columns = {c.name for c in AICall.__table__.columns}
        assert "id" in columns
        assert "request_id" in columns
        assert "scene" in columns
        assert "provider" in columns
        assert "model" in columns
        assert "status" in columns

    def test_user_model_instantiation(self):
        """User 模型可以正确实例化"""
        user = User(phone="13800138000", nickname="测试用户")
        assert user.phone == "13800138000"
        assert user.nickname == "测试用户"

    def test_conversation_model_instantiation(self):
        """Conversation 模型可以正确实例化"""
        user_id = uuid.uuid4()
        conv = Conversation(user_id=user_id, active_role="student", title="测试会话")
        assert conv.user_id == user_id
        assert conv.active_role == "student"
        assert conv.title == "测试会话"

    def test_message_model_instantiation(self):
        """Message 模型可以正确实例化"""
        conv_id = uuid.uuid4()
        msg = Message(
            conversation_id=conv_id,
            client_msg_id="client-123",
            role="user",
            content="你好",
        )
        assert msg.conversation_id == conv_id
        assert msg.role == "user"
        assert msg.content == "你好"

    def test_role_binding_instantiation(self):
        """RoleBinding 模型可以正确实例化"""
        user_id = uuid.uuid4()
        rb = RoleBinding(user_id=user_id, role="student", verified=False)
        assert rb.user_id == user_id
        assert rb.role == "student"
        assert rb.verified is False

    def test_soft_delete_mixin(self):
        """SoftDeleteMixin 提供 deleted_at 字段"""
        assert hasattr(User, "deleted_at")
        assert hasattr(Conversation, "deleted_at")
        assert hasattr(Message, "deleted_at")

    def test_timestamp_mixin(self):
        """TimestampMixin 提供 id/created_at/updated_at 字段"""
        assert hasattr(User, "id")
        assert hasattr(User, "created_at")
        assert hasattr(User, "updated_at")

    def test_base_is_declarative(self):
        """Base 是 SQLAlchemy 声明式基类"""
        from sqlalchemy.orm import DeclarativeBase

        assert issubclass(Base, DeclarativeBase)
