"""Match certificates (§20). Every approved/reviewed pair gets a
persistable certificate recording exactly why, with versions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from ..core.clock import utcnow
from ..core.types import Independence, NormalizedContract
from .deterministic_matcher import DeterministicResult, MATCHER_VERSION
from .llm_matcher import LLMMatchResult
from .rules_validator import ValidationOutcome


@dataclass
class MatchCertificate:
    match_id: str
    contract_a: str          # venue:contract_id
    contract_b: str
    checks: dict[str, bool]
    independence: str
    llm_confidence: float
    deterministic_confidence: float
    matcher_version: str
    llm_model_version: str
    llm_prompt_version: str
    status: str              # APPROVED / REJECTED / REVIEW
    reasons: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)

    def to_json(self) -> str:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return json.dumps(d)

    def render(self) -> str:
        lines = ["MATCH CERTIFICATE", "",
                 f"Match ID: {self.match_id}",
                 f"Contract A: {self.contract_a}",
                 f"Contract B: {self.contract_b}", ""]
        for check, ok in self.checks.items():
            lines.append(f"{check}: {'PASS' if ok else 'FAIL'}")
        lines += ["",
                  f"Underlying venue relationship: {self.independence}",
                  f"LLM confidence: {self.llm_confidence:.4f}",
                  f"Deterministic confidence: {self.deterministic_confidence:.4f}",
                  "",
                  f"Matcher version: {self.matcher_version}",
                  f"LLM model version: {self.llm_model_version}",
                  f"LLM prompt version: {self.llm_prompt_version}",
                  "",
                  f"Status: {self.status}"]
        if self.reasons:
            lines += ["", "Reasons:"] + [f"  - {r}" for r in self.reasons]
        return "\n".join(lines)


def build_certificate(a: NormalizedContract, b: NormalizedContract,
                      det: DeterministicResult, llm: LLMMatchResult,
                      outcome: ValidationOutcome) -> MatchCertificate:
    ca = f"{a.venue}:{a.contract_id}"
    cb = f"{b.venue}:{b.contract_id}"
    match_id = hashlib.sha256(
        f"{ca}|{cb}|{MATCHER_VERSION}|{llm.prompt_version}".encode()
    ).hexdigest()[:16]
    return MatchCertificate(
        match_id=match_id,
        contract_a=ca,
        contract_b=cb,
        checks=dict(det.checks),
        independence=outcome.independence.value,
        llm_confidence=llm.confidence,
        deterministic_confidence=det.confidence,
        matcher_version=det.version,
        llm_model_version=llm.model_version,
        llm_prompt_version=llm.prompt_version,
        status=outcome.status,
        reasons=list(outcome.reasons),
    )
