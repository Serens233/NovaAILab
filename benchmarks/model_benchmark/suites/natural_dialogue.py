"""Reserved natural-dialogue evaluation interface.

Performance prompts are single-turn by design.  Natural dialogue needs
multi-turn fixtures, a user simulator or scripted turns, and a rubric for
coherence, responsiveness, and non-mechanical comfort.  These are deliberately
separate from the latency benchmark so a throughput result is never presented
as a quality result.
"""

from __future__ import annotations

from .base import ReservedSuite


class NaturalDialogueSuite(ReservedSuite):
    id = "natural_dialogue"
    description = "Evaluate multi-turn coherence, responsiveness, and conversational naturalness."
    required_inputs = (
        "dialogue_case_set",
        "turn_simulator",
        "judge_rubric",
        "human_review_sample",
    )
    reserved_reason = (
        "Natural-dialogue scoring needs scripted multi-turn cases and a reviewed "
        "rubric; the interface is reserved and does not call a model."
    )
    case_schema = {
        "id": "string",
        "topic": "string",
        "turns": "list[{role, content}]",
        "probe_after_turns": "int",
        "rubric": "string",
        "expected_signals": "list[string]",
    }
