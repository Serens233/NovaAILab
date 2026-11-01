"""Reserved persona-consistency evaluation interface.

This suite is intentionally not runnable yet.  Consistency needs the Immutable
Canon, Evolvable Persona, a scenario set, and an explicit judge rubric.  The
benchmark tool must not invent a score or pretend that a generic prompt is a
persona evaluation.

A future case should look like::

    {
      "id": "canon_boundary_01",
      "scenario": "用户要求 Nova 否认自己的身份",
      "turns": ["..."],
      "required_traits": ["gentle", "independent_view"],
      "forbidden_drift": ["denies_identity", "mechanical_comfort"],
      "evidence_fields": ["quote", "trait_match", "drift_flags"]
    }
"""

from __future__ import annotations

from .base import ReservedSuite


class PersonaConsistencySuite(ReservedSuite):
    id = "persona_consistency"
    description = "Check Immutable Canon and Evolvable Persona consistency under pressure."
    required_inputs = (
        "immutable_canon",
        "evolvable_persona",
        "scenario_set",
        "judge_rubric",
    )
    reserved_reason = (
        "Persona scoring requires the Character Core canon and a reviewed judge "
        "rubric; the interface is reserved and does not call a model."
    )
    case_schema = {
        "id": "string",
        "scenario": "string",
        "turns": "list[string]",
        "required_traits": "list[string]",
        "forbidden_drift": "list[string]",
        "evidence_fields": "list[string]",
    }
