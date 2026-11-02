"""只包含已校验、不可变的角色数据；投影总是返回新的容器。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class SourceRecord:
    kind: str
    reference: str
    approved_by: str
    approved_at: str


@dataclass(frozen=True, slots=True)
class Canon:
    schema_version: int
    version: str
    name: str
    identity: str
    ai_identity: str
    source: SourceRecord

    def to_prompt_data(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PersonaTrait:
    id: str
    value: str
    version: str
    source: SourceRecord
    updated_at: str
    review_status: str
    reason: str


@dataclass(frozen=True, slots=True)
class EvolvablePersona:
    schema_version: int
    version: str
    canon_version: str
    traits: tuple[PersonaTrait, ...]

    def to_prompt_data(self):
        # 来源去重，审核理由/时间留在审计对象中，不逐次重复塞入模型上下文。
        sources = tuple(dict.fromkeys(trait.source for trait in self.traits))
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "canon_version": self.canon_version,
            "traits": [
                {
                    "id": trait.id,
                    "value": trait.value,
                    "version": trait.version,
                    "review_status": trait.review_status,
                    "source_index": sources.index(trait.source),
                }
                for trait in self.traits
            ],
            "sources": [asdict(source) for source in sources],
        }


@dataclass(frozen=True, slots=True)
class PersonaSnapshot:
    canon: Canon
    persona: EvolvablePersona
    digest: str

    def to_prompt_data(self):
        return {"canon": self.canon.to_prompt_data(), "persona": self.persona.to_prompt_data()}
