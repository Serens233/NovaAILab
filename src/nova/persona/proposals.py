"""未来成长流程的内部数据契约；Phase 2 不执行审批、应用或后台反思。"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from nova.persona.manager import PersonaPermissionError
from nova.persona.models import PersonaSnapshot


@dataclass(frozen=True, slots=True)
class GrowthProposal:
    id: str
    source_kind: str
    source_reference: str
    target_attribute: str
    proposed_value: str
    evidence_references: tuple[str, ...]
    base_canon_version: str
    base_persona_version: str
    reason: str
    created_at: str
    review_status: str = "proposed"
    approval_subject: None = None
    processing_result: None = None

    def __post_init__(self):
        # 直接构造也不能伪造已批准状态；该契约不承载运行时写入权限。
        if (
            self.review_status != "proposed"
            or self.approval_subject is not None
            or self.processing_result is not None
        ):
            raise PersonaPermissionError()
        if self.source_kind not in {"model", "human", "system"}:
            raise ValueError("人格提案来源无效")
        for value, maximum in (
            (self.source_reference, 300),
            (self.target_attribute, 80),
            (self.proposed_value, 200),
            (self.reason, 300),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > maximum
                or "\x00" in value
            ):
                raise ValueError("人格提案字段无效")
        if (
            not isinstance(self.evidence_references, tuple)
            or not 1 <= len(self.evidence_references) <= 16
        ):
            raise ValueError("人格提案需要有界的证据引用")
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 300
            for value in self.evidence_references
        ):
            raise ValueError("人格提案证据无效")
        for value in (self.base_canon_version, self.base_persona_version):
            if not isinstance(value, str) or not re.fullmatch(r"1\.[0-9]{1,4}\.[0-9]{1,4}", value):
                raise ValueError("人格提案版本无效")


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    proposal_id: str
    review_status: str
    valid: bool
    evidence_references: tuple[str, ...]
    issues: tuple[str, ...]
    validator: str = "phase2_contract_v1"
    approval_subject: None = None
    processing_result: str = "not_applied"


def create_proposal(
    *,
    source_kind,
    source_reference,
    target_attribute,
    proposed_value,
    evidence_references,
    snapshot,
    reason,
):
    if not isinstance(evidence_references, (list, tuple)):
        raise ValueError("人格提案需要证据引用列表")
    return GrowthProposal(
        id=str(uuid4()),
        source_kind=source_kind,
        source_reference=source_reference,
        target_attribute=target_attribute,
        proposed_value=proposed_value,
        evidence_references=tuple(evidence_references),
        base_canon_version=snapshot.canon.version,
        base_persona_version=snapshot.persona.version,
        reason=reason,
        created_at=datetime.now(UTC).isoformat(),
    )


def validate_proposal(proposal: GrowthProposal, snapshot: PersonaSnapshot):
    """仅验证契约和版本；valid不表示获准，也不会改变任何人格快照。"""
    issues = []
    if (proposal.base_canon_version, proposal.base_persona_version) != (
        snapshot.canon.version,
        snapshot.persona.version,
    ):
        issues.append("BASE_VERSION_CONFLICT")
    supported = {"persona." + trait.id for trait in snapshot.persona.traits}
    if proposal.target_attribute not in supported:
        issues.append("TARGET_NOT_EVOLVABLE")
    return ProposalValidation(
        proposal.id,
        "validated" if not issues else "rejected",
        not issues,
        proposal.evidence_references,
        tuple(issues),
    )


def approve_proposal(*args, **kwargs):
    """当前没有安全的管理审批入口；负责人通过源控配置审核/部署生效。"""
    raise PersonaPermissionError()


def apply_proposal(*args, **kwargs):
    raise PersonaPermissionError()
