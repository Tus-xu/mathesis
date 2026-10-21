"""Math Arena research experiment sandbox (WP-I / RES-D).

This package is the *only* sanctioned place where LLM-generated or caller-supplied
Python may be executed for research experiments. It exposes:

* a typed execution contract (:mod:`sandbox.models`) whose result always carries a
  :class:`~sandbox.models.ExecutionManifest` (code/input/output/dependency hashes,
  image digest, seed, timings, exit code, isolation mode, per-control enforcement),
* pluggable isolation backends (:mod:`sandbox.backends`) -- a real container backend
  and a deliberately-honest local/process backend,
* a machine-readable capability report (:mod:`sandbox.capabilities`) that answers
  "what isolation is actually in force right now?" without overstating it,
* a typed job-handler interface (:mod:`sandbox.jobhandler`) for the durable Job
  Runtime plus a standalone Valkey Stream consumer (:mod:`sandbox.worker`).

Design rule for this package, stated once and enforced everywhere: **never claim
isolation that is not enforced**. Every control carries a boolean plus the exact
mechanism and the exact reason when it is not enforced.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
