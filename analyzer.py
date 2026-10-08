"""google-genai(신규 SDK) 기반 Gemini Vision 분석 및 JSON 파싱."""

from __future__ import annotations

import json
import os
import re

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """\
이미지 내 마우스 커서 주변의 텍스트나 질문을 빠르게 분석하세요.
이미지의 빨간 원과 십자 표시가 마우스 커서 위치입니다. 그 위치에 가장 가까운 내용을 우선 분석하세요.

규칙:
1. 객관식 문제나 선택지가 있는 경우: 가장 타당한 번호나 핵심 키워드를 최우선으로 간결하게 추출하세요.
   (예: "3", "②", "B", "3번 광합성")
2. 주관식/단답형인 경우: 1~3단어 이내의 핵심 정답을 추출하세요.
3. 질문이 아닌 일반 텍스트인 경우: 핵심 키워드를 1~3단어로 추출하세요.
4. 판독할 수 있는 내용이 없으면 result는 "인식 불가"로 하세요.
5. summary는 근거나 내용을 담은 한국어 1줄 요약(60자 이내)으로 작성하세요.
6. 반드시 아래 JSON 구조로만 응답하세요. 다른 텍스트는 출력하지 마세요.
   {"result": "정답 번호 또는 핵심 단어", "summary": "1줄 요약"}
"""

USER_PROMPT = "커서 주변 내용을 분석해 JSON으로 답하세요."


class AnalysisResult(BaseModel):
    result: str = Field(description="정답 번호 또는 핵심 단어 (1~3단어)")
    summary: str = Field(description="1줄 요약")


class AnalyzerError(RuntimeError):
    """분석 실패 시 메뉴바에 표시할 짧은 메시지를 담는 예외."""


def _parse_json_text(text: str | None) -> AnalysisResult:
    """response.parsed가 비어 있을 때를 위한 보조 파서."""
    if not text:
        raise AnalyzerError("빈 응답")

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)

    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            try:
                return AnalysisResult(
                    result=str(data.get("result", "")).strip(),
                    summary=str(data.get("summary", "")).strip(),
                )
            except ValidationError:
                continue

    raise AnalyzerError("JSON 파싱 실패")


class Analyzer:
    def __init__(self, model: str | None = None) -> None:
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            raise AnalyzerError("GEMINI_API_KEY 없음")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        # 환경 변수 GEMINI_API_KEY를 자동으로 읽는다.
        self._client = genai.Client()
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AnalysisResult,
            temperature=0.2,
            max_output_tokens=256,
            # 저지연을 위해 사고(thinking) 토큰을 끈다.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    def analyze(self, png_bytes: bytes) -> AnalysisResult:
        image = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[image, USER_PROMPT],
                config=self._config,
            )
        except errors.APIError as exc:
            raise AnalyzerError(f"API 오류 {exc.code}") from exc

        parsed = response.parsed
        if isinstance(parsed, AnalysisResult):
            result = parsed
        elif isinstance(parsed, dict):
            result = AnalysisResult(
                result=str(parsed.get("result", "")), summary=str(parsed.get("summary", ""))
            )
        else:
            result = _parse_json_text(response.text)

        result.result = result.result.strip() or "인식 불가"
        result.summary = " ".join(result.summary.split())
        return result
