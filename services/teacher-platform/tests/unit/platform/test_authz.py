"""AuthZ（G3 防 BOLA）纯逻辑单测：横向越权/同校放行/空资源放行。"""
from __future__ import annotations

import pytest

from app.core.authz import ResourceOwner, assert_resource_owner
from app.core.errors import ApiError, ErrorCode
from app.core.security import UserToken

teacher_a = UserToken(user_id="t-a", name="A", role="teacher", school_id="s-1")
teacher_b = UserToken(user_id="t-b", name="B", role="teacher", school_id="s-2")
admin = UserToken(user_id="adm", name="Admin", role="admin", school_id="s-1")


class TestAssertResourceOwner:
    def test_same_school_ok(self):
        assert_resource_owner(teacher_a, ResourceOwner(school_id="s-1"))

    def test_cross_school_denied(self):
        with pytest.raises(ApiError) as ei:
            assert_resource_owner(teacher_a, ResourceOwner(school_id="s-2"))
        assert ei.value.code == ErrorCode.ROLE_DENIED

    def test_owner_match_ok(self):
        assert_resource_owner(teacher_a, ResourceOwner(school_id="s-1", allowed_teacher_ids={"t-a"}))

    def test_owner_mismatch_denied(self):
        with pytest.raises(ApiError) as ei:
            assert_resource_owner(teacher_a, ResourceOwner(school_id="s-1", allowed_teacher_ids={"t-b"}))
        assert ei.value.code == ErrorCode.ROLE_DENIED

    def test_admin_passes_all(self):
        assert_resource_owner(admin, ResourceOwner(school_id="s-9"))

    def test_dict_resource(self):
        assert_resource_owner(teacher_a, {"school_id": "s-1", "owner_id": "t-a"})

    def test_none_resource_allowed(self):
        assert_resource_owner(teacher_a, None)
