"""RV04 unit · literature job handler registration."""

from __future__ import annotations

from research_vnext.core.worker.runtime import get_handler, known_job_types
from research_vnext.core.worker.literature_handlers import literature_ingest_handler


def test_literature_ingest_handler_is_registered():
    assert "literature.ingest" in known_job_types()
    assert get_handler("literature.ingest") is literature_ingest_handler
