"""Reserved memory-accuracy evaluation interface.

Memory tests must use isolated synthetic facts and never the user's private
conversations.  A future runner should create a fresh thread/model context,
insert facts, run distractor turns, then probe recall and provenance.
"""

from __future__ import annotations

from .base import ReservedSuite


class MemoryAccuracySuite(ReservedSuite):
    id = "memory_accuracy"
    description = "Measure recall and provenance using isolated synthetic memory fixtures."
    required_inputs = (
        "synthetic_memory_fixture",
        "probe_set",
        "storage_adapter",
        "judge_rubric",
    )
    reserved_reason = (
        "Memory-accuracy scoring requires the Phase 3 memory API and isolated "
        "synthetic fixtures; the interface is reserved and does not touch user data."
    )
    case_schema = {
        "id": "string",
        "facts": "list[string]",
        "distractor_turns": "list[string]",
        "probes": "list[{question, expected_fact_id, provenance_required}]",
        "rubric": "string",
    }
