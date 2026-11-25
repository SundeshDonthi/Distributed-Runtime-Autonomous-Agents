from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Requirement = Literal['async_client', 'http2', 'permissive_license']
Candidate = Literal['encode/httpx', 'psf/requests', 'aio-libs/aiohttp']
Source = Literal['snapshot', 'live']


class InvestigationSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    candidates: list[Candidate] = Field(default_factory=lambda: ['encode/httpx', 'psf/requests', 'aio-libs/aiohttp'], min_length=1, max_length=3)
    requirements: list[Requirement] = Field(default_factory=lambda: ['async_client', 'permissive_license'], min_length=1, max_length=3)
    source: Source = 'snapshot'

    @model_validator(mode='after')
    def unique_entries(self):
        if len(set(self.candidates)) != len(self.candidates) or len(set(self.requirements)) != len(self.requirements):
            raise ValueError('candidates and requirements must be unique')
        return self


class Citation(BaseModel):
    url: str
    excerpt: str
    source: Source
    revision: str | None = None


class Finding(BaseModel):
    requirement: Requirement
    status: Literal['met', 'not_met', 'not_established']
    explanation: str
    citations: list[Citation] = Field(default_factory=list)


class CollectionArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository: Candidate
    source: Source
    kind: Literal['metadata', 'documentation']


class EvaluationArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository: Candidate
    source: Source
    requirements: list[Requirement] = Field(min_length=1, max_length=3)
    dependency_outputs: dict[str, dict]


class AssemblyArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Source
    requirements: list[Requirement] = Field(min_length=1, max_length=3)
    dependency_outputs: dict[str, dict]
