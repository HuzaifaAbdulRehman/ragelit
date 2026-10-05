import hashlib
import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator

from app.audits.contracts import AuditModel, Checksum
from app.chat.contracts import Generation
from app.chat.provider import SYSTEM, CompatibleProvider
from app.core.config import Settings
from app.retrieval.contracts import AuthorizedChunk

_FACT = re.compile(r"\bFACTANSWER[0-9a-f]{32}\b")
_ATTACK = re.compile(r"\bAUDITCANARY[0-9a-f]{32}\b")
_LOCAL = re.compile(r"http://(?:127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?/v1/?")


class InjectionProvider:
    def __init__(self, profile: Literal["resistant", "obeying", "deny_all"]) -> None:
        if profile not in {"resistant", "obeying", "deny_all"}:
            raise ValueError("invalid injection profile")
        self.profile = profile

    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        if self.profile == "deny_all" or not context:
            return Generation("", ())
        answers = []
        for chunk in context:
            facts = _FACT.findall(chunk.text)
            if len(facts) != 1:
                raise ValueError("injection_fixture_invalid")
            answers.append(facts[0])
            if self.profile == "obeying":
                answers.extend(_ATTACK.findall(chunk.text))
        return Generation(" ".join(answers), tuple(chunk.id for chunk in context))


class LocalInjectionConfiguration(AuditModel):
    base_url: str = Field(max_length=100, repr=False)
    model: str = Field(pattern=r"^[A-Za-z0-9_./:-]{1,160}$")
    weights_hash: Checksum

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if not _LOCAL.fullmatch(self.base_url):
            raise ValueError("invalid local injection endpoint")
        port = urlsplit(self.base_url).port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("invalid local injection endpoint")
        return self

    def provider(self, settings: Settings) -> CompatibleProvider:
        if settings.environment not in {"local", "test"}:
            raise ValueError("injection_local_environment_required")
        return CompatibleProvider(
            settings.model_copy(
                update={
                    "llm_base_url": AnyHttpUrl(self.base_url),
                    "llm_model": self.model,
                    "llm_api_key": SecretStr(""),
                    "llm_timeout_seconds": 30,
                }
            )
        )

    def metadata(self) -> dict[str, str | int]:
        return {
            "model": self.model,
            "weights_hash": self.weights_hash,
            "base_url": self.base_url,
            "prompt_hash": hashlib.sha256(SYSTEM.encode()).hexdigest(),
            "temperature": 0,
            "max_tokens": 1024,
            "response_format": "json_object",
        }
