"""教师备课班级目录必须来自 8000 身份权威，而非 8100 的孤立副本。"""

from app.domains.catalog.service import normalize_authorized_class_items


def test_normalize_authorized_classes_accepts_api_response_envelope() -> None:
    payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "id": "class-1",
                    "name": "高一(1)班",
                    "grade": "高一",
                    "myRole": "teacher",
                    "confirmed": True,
                },
                {
                    "id": "class-2",
                    "name": "待确认班",
                    "myRole": "teacher",
                    "confirmed": False,
                },
            ]
        },
    }

    assert normalize_authorized_class_items(payload) == [
        {"class_id": "class-1", "name": "高一(1)班", "students": 0}
    ]


def test_normalize_authorized_classes_does_not_expose_student_or_unconfirmed_rows() -> None:
    payload = {
        "items": [
            {"id": "student-class", "name": "学生班", "myRole": "student", "confirmed": True},
            {"id": "assistant-class", "name": "助教班", "myRole": "assistant", "confirmed": True},
            {"name": "缺少 ID", "myRole": "teacher", "confirmed": True},
        ]
    }

    assert normalize_authorized_class_items(payload) == [
        {"class_id": "assistant-class", "name": "助教班", "students": 0}
    ]
