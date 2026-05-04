from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from contextspy.analysis.tokenizer import get_token_strings

router = APIRouter(tags=["tokenize"])

_MAX_TEXTS = 200
_MAX_CHARS = 50_000


class TokenizeRequest(BaseModel):
    texts: list[str]

    @field_validator("texts")
    @classmethod
    def limit_texts(cls, v: list[str]) -> list[str]:
        return v[:_MAX_TEXTS]


class TokenizeResponse(BaseModel):
    results: list[list[str]]


@router.post("/tokenize", response_model=TokenizeResponse)
def tokenize(body: TokenizeRequest) -> TokenizeResponse:
    results = [get_token_strings(t[:_MAX_CHARS]) for t in body.texts]
    return TokenizeResponse(results=results)
