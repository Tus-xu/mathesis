"""Acceptance condition #2: content hashes are deterministic and timestamp-independent.

The test the mandate asks for explicitly: run the same trivial job twice and assert
``code_hash``/``input_hash`` are identical while ``executed_at``/``duration_ms``
differ. It runs on the **local** backend so it needs no container, and additionally
on the container backend when one is available.
"""

from __future__ import annotations

import pytest

from sandbox.hashing import (
    canonical_json,
    code_hash,
    dependency_lock_hash,
    input_hash,
    output_hash,
    sha256_text,
)
from sandbox.models import DependencyLock, InputRef
from sandbox.orchestrator import run_experiment

from .conftest import TEST_IMAGE, make_request

CODE = "import math\nprint('determinism', round(math.pi, 6))\n"


def _local_run(run_id: str):
    req = make_request(
        CODE,
        run_id=run_id,
        backend="local",
        require_real_isolation=False,
        inputs=[
            InputRef(name="a.txt", inline="hello\n", sha256=sha256_text("hello\n")),
            InputRef(name="b.txt", inline="world\n", sha256=sha256_text("world\n")),
        ],
    )
    return run_experiment(req)


def test_two_runs_share_content_hashes_but_not_timestamps() -> None:
    first = _local_run("det-1")
    second = _local_run("det-2")

    m1, m2 = first.manifest, second.manifest

    # Content identity: identical.
    assert m1.code_hash == m2.code_hash, "code_hash must depend on code content only"
    assert m1.input_hash == m2.input_hash, "input_hash must depend on input content only"
    assert m1.output_hash == m2.output_hash, "output_hash must depend on captured output only"
    assert m1.dependency_lock_hash == m2.dependency_lock_hash

    # Run identity: different (this is what proves we hash content, not timestamps).
    assert m1.run_id != m2.run_id
    assert m1.executed_at != m2.executed_at, "two runs must not share a wall-clock timestamp"
    assert m1.duration_ms >= 0 and m2.duration_ms >= 0

    # Sanity: the statuses are real executions, not refusals.
    assert m1.exec_status == "pass" and m2.exec_status == "pass"
    assert "determinism" in first.stdout


def test_code_hash_is_insensitive_to_line_ending_but_sensitive_to_content() -> None:
    assert code_hash("a\nb") == code_hash("a\r\nb"), "CRLF checkout must hash like LF"
    assert code_hash("a\nb") != code_hash("a\nb\n")
    assert code_hash("x=1") != code_hash("x =1")


def test_input_hash_is_order_independent_but_content_dependent() -> None:
    a = InputRef(name="a", inline="A", sha256=sha256_text("A"))
    b = InputRef(name="b", inline="B", sha256=sha256_text("B"))
    assert input_hash([a, b]) == input_hash([b, a])
    c = InputRef(name="b", inline="B!", sha256=sha256_text("B!"))
    assert input_hash([a, b]) != input_hash([a, c])


def test_output_hash_changes_with_output() -> None:
    assert output_hash("x\n", "") == output_hash("x\n", "")
    assert output_hash("x\n", "") != output_hash("y\n", "")


def test_dependency_lock_hash_distinguishes_empty_lock_from_image_pin() -> None:
    empty = DependencyLock(format="none")
    pinned = DependencyLock(format="image-pin", image=TEST_IMAGE)
    assert dependency_lock_hash(empty) != dependency_lock_hash(pinned), (
        "an unset lock must not hash the same as an image-pinned lock, or 'no lock' "
        "would read as 'pinned dependency set'"
    )
    assert dependency_lock_hash(pinned) == dependency_lock_hash(
        DependencyLock(format="image-pin", image=TEST_IMAGE)
    )


def test_canonical_json_is_stable_across_key_order() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


@pytest.mark.container
def test_container_runs_are_also_hash_stable(container_backend) -> None:
    """Same assertion against the container backend: hashes must not depend on isolation."""
    payload = {
        "inputs": [InputRef(name="a.txt", inline="hello\n", sha256=sha256_text("hello\n"))],
    }
    req1 = make_request(CODE, run_id="det-c1", backend="container", **payload)
    req2 = make_request(CODE, run_id="det-c2", backend="container", **payload)
    m1 = run_experiment(req1, backend=container_backend).manifest
    m2 = run_experiment(req2, backend=container_backend).manifest

    assert m1.exec_status == "pass", m1.warnings
    assert m2.exec_status == "pass", m2.warnings
    assert m1.code_hash == m2.code_hash
    assert m1.input_hash == m2.input_hash
    assert m1.output_hash == m2.output_hash
    assert m1.image_digest == m2.image_digest
    assert m1.image_digest is not None and m1.image_digest.startswith("sha256:")
    assert m1.executed_at != m2.executed_at
