"""SQLAlchemy 模型包

__init__.py 里 import 全部 model 供 alembic autogenerate。
"""

from app.models.agent_run import AgentRun, AgentStep, ToolInvocation
from app.models.ai_call import AICall
from app.models.ai_quality_score import AIQualityScore
from app.models.ai_recommendation import AIRecommendation
from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.chunk import Chunk
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.classroom import ClassroomSession
from app.models.conversation import Conversation
from app.models.course import Course
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    DailyQuestion,
    ErrorRecord,
    MasteryRecord,
    Quiz,
    QuizItem,
    Streak,
    Submission,
    SubmissionItem,
)
from app.models.episodic_memory import EpisodicMemory
from app.models.event import Event
from app.models.exam_paper import ExamPaper, ExamPaperItem
from app.models.file import File, FileAsset
from app.models.growth import KpPrerequisite, UserDailyStat
from app.models.identity import (
    AccountDeletionRequest,
    AuthRefreshToken,
    AuthSession,
    IdentityAuditLog,
    Organization,
    OrganizationInvite,
    RoleApplication,
    UserConsent,
    UserCredential,
)
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.models.learning_event import LearningEvent
from app.models.m2_logs import (
    KbEvalRun,
    RouterEvalLog,
    SearchLog,
    SpeechLog,
    XingchenKbMapping,
)
from app.models.mastery_snapshot import MasterySnapshot
from app.models.message import Message
from app.models.question_bank import QuestionBank

# 【已隔离】上一代科研端表模型（app/models/research.py）于 2026-09-17 移出，
# 详见 D:\research-legacy-quarantine\README.md。
from app.models.role_binding import RoleBinding
from app.models.skill import Skill
from app.models.skill_run import SkillRun
from app.models.student_profile import StudentProfile
from app.models.system_config import SystemConfig
from app.models.task import Notification, Task
from app.models.teacher import (
    ActionableInsight,
    ClassroomMode,
    TeacherAction,
    TeacherTask,
    TeachingArtifact,
)
from app.models.trial import TrialFeedback
from app.models.tutor_session import TutorSession
from app.models.user import User
from app.models.user_integration_config import UserIntegrationConfig
from app.models.user_model_config import UserModelConfig
from app.models.user_profile import UserProfile

__all__ = [
    "Base",
    "SoftDeleteMixin",
    "TimestampMixin",
    # M0-M1
    "User",
    "RoleBinding",
    "UserCredential",
    "AuthSession",
    "AuthRefreshToken",
    "RoleApplication",
    "Organization",
    "OrganizationInvite",
    "IdentityAuditLog",
    "UserConsent",
    "AccountDeletionRequest",
    "Class",
    "ClassMember",
    "Conversation",
    "Message",
    "UserProfile",
    "UserModelConfig",
    "UserIntegrationConfig",
    "EpisodicMemory",
    "KnowledgePoint",
    "KnowledgeDoc",
    "Chunk",
    "Skill",
    "SkillRun",
    "AICall",
    "AIQualityScore",
    "Event",
    # M2 管理后台系统配置
    "SystemConfig",
    # M2 任务中心 / 站内通知
    "Task",
    "Notification",
    # M2 文件域
    "File",
    "FileAsset",
    # M2 流水表
    "SpeechLog",
    "SearchLog",
    "XingchenKbMapping",
    "KbEvalRun",
    "RouterEvalLog",
    # M2 教学任务域
    "Quiz",
    "QuizItem",
    "Submission",
    "SubmissionItem",
    "DailyQuestion",
    "Streak",
    "MasteryRecord",
    "MasterySnapshot",
    "Assignment",
    "AssignmentTarget",
    "ErrorRecord",
    # M2 引导式解题
    "TutorSession",
    # 体验测试留痕（作品验收：试用反馈 + 身份标注）
    "TrialFeedback",
    # M2 双师课堂（迭代05 阶段4）
    "Course",
    # M2 迭代 AI 数学课堂（OpenMAIC 融合改造，两段式生成：大纲→逐页内容）
    "ClassroomSession",
    # M2 结构化题库（题库优先）
    "QuestionBank",
    # M2 迭代16 学情增长域
    "UserDailyStat",
    "KpPrerequisite",
    # M2 迭代17 AI 管家化
    "StudentProfile",
    "LearningEvent",
    "AIRecommendation",
    "ExamPaper",
    "ExamPaperItem",
    # Butler Kernel v2 运行账本
    "AgentRun",
    "AgentStep",
    "ToolInvocation",
    # M3 教师端
    "TeachingArtifact",
    "ActionableInsight",
    "TeacherAction",
    "TeacherTask",
    "ClassroomMode",
    # 科研端（论文阅读与写作平台）
    "ResearchPaper",
    "ResearchCollection",
    "ResearchPaperCollection",
    "ResearchTag",
    "ResearchPaperTag",
    "ResearchReadingPosition",
    "ResearchAnnotation",
    "ResearchNote",
    "ResearchCitation",
    "ResearchKbChunk",
    "ResearchMathObject",
    "ResearchManuscript",
    "ResearchManuscriptVersion",
    "ResearchWritingMaterial",
    "ResearchManuscriptCitation",
    "ResearchAiSuggestion",
    "ResearchTask",
    # 科研端 AI 对话记忆
    "ResearchChatSession",
    "ResearchChatMessage",
]
