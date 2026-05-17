# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
