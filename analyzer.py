"""google-genai(신규 SDK) 기반 Gemini Vision 분석 및 JSON 파싱."""

from __future__ import annotations

import json
import os
import re
from typing import Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError


def _fix_ssl_cert_env() -> None:
    """py2app 번들의 부트 코드는 SSL_CERT_FILE/SSL_CERT_DIR을 '.../openssl.ca/no-such-file'로
    설정한다. google-genai는 이 값을 certifi보다 우선해 ssl.create_default_context()에 넘기므로
    FileNotFoundError([Errno 2])로 클라이언트 생성이 실패한다. 존재하지 않는 경로는 지우고
    certifi 번들을 쓰게 한다."""
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        path = os.environ.get(name)
        if path and not os.path.exists(path):
            del os.environ[name]
    if "SSL_CERT_FILE" not in os.environ:
        try:
            import certifi

            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:  # certifi가 없으면 google-genai의 기본 동작에 맡긴다.
            pass


_fix_ssl_cert_env()

DEFAULT_MODEL = "gemini-3.1-flash-lite"

# 사고(thinking) 토큰 예산. 0이면 끄고(가장 빠름), 어려운 문제(킬러 문항)는 1024~4096 권장.
# Flash-Lite 계열 일부(gemini-3.5-flash-lite, gemini-flash-lite-latest)는 0을 거부(400)하므로 1 이상으로 둔다.
THINKING_BUDGET = 0
# 답(JSON) 자체에 필요한 출력 토큰. 사고 토큰은 이와 별도로 더해 준다.
ANSWER_MAX_TOKENS = 2048

SYSTEM_PROMPT = """\
이미지 내 마우스 커서 주변의 텍스트나 질문을 빠르게 분석하세요.
이미지의 빨간 원과 십자 표시가 마우스 커서 위치입니다. 그 위치에 가장 가까운 내용을 우선 분석하세요.

규칙:
1. 번호가 붙은 객관식 문제나 선택지가 있는 경우: answer_type은 "choice"로 하고,
   result에는 가장 타당한 선택지 번호를 아라비아 숫자 하나로만 쓰세요. (예: "3")
   선택지가 알파벳(A, B, C, D, E)이나 한글(ㄱ, ㄴ, ㄷ / 가, 나, 다)로 구분돼 있으면
   순서대로 1, 2, 3, 4, 5로 바꿔 숫자로 쓰세요. (예: B → "2", ㄷ → "3")
2. 주관식/단답형인 경우: answer_type은 "text"로 하고, 1~3단어 이내의 핵심 정답을 추출하세요.
3. 질문이 아닌 일반 텍스트인 경우: answer_type은 "text"로 하고, 핵심 키워드를 1~3단어로 추출하세요.
4. 판독할 수 있는 내용이 없으면 answer_type은 "text", result는 "인식 불가"로 하세요.
5. summary는 근거나 내용을 담은 한국어 1줄 요약(60자 이내)으로 작성하세요.
6. 반드시 아래 JSON 구조로만 응답하세요. 다른 텍스트는 출력하지 마세요.
   {"answer_type": "choice 또는 text", "result": "정답 번호 또는 핵심 단어", "summary": "1줄 요약"}
"""

USER_PROMPT = "커서 주변 내용을 분석해 JSON으로 답하세요."


AnswerType = Literal["choice", "text"]

# 원문자/괄호 숫자 → 아라비아 숫자
_CIRCLED_DIGITS = str.maketrans(
    {
        **{c: str(i) for i, c in enumerate("①②③④⑤", start=1)},
        **{c: str(i) for i, c in enumerate("❶❷❸❹❺", start=1)},
        **{c: str(i) for i, c in enumerate("➀➁➂➃➄", start=1)},
        **{c: str(i) for i, c in enumerate("⑴⑵⑶⑷⑸", start=1)},
        **{c: str(i) for i, c in enumerate("１２３４５", start=1)},
    }
)

# 알파벳/한글 선택지 기호 → 아라비아 숫자 (A=1 … E=5)
_LETTER_CHOICES = {
    **{c: str(i) for i, c in enumerate("ABCDE", start=1)},
    **{c: str(i) for i, c in enumerate("abcde", start=1)},
    **{c: str(i) for i, c in enumerate("ＡＢＣＤＥ", start=1)},
    **{c: str(i) for i, c in enumerate("ㄱㄴㄷㄹㅁ", start=1)},
    **{c: str(i) for i, c in enumerate("가나다라마", start=1)},
}
_LETTER_CLASS = "".join(_LETTER_CHOICES)

# 선택지 기호 단독 또는 기호 뒤에 구분자가 오는 형태: "B", "(B)", "B.", "B)", "B번", "B 광합성", "정답: B"
# "Apple", "가방" 같은 단어의 첫 글자는 건드리지 않는다.
_LETTER_CHOICE_PATTERN = re.compile(
    rf"^(?:(?:정답|답|answer)\s*(?:은|는|[:：])?\s*)?[(\[]?([{_LETTER_CLASS}])(?:[)\].]|\s*번|\s|$)",
    re.IGNORECASE,
)


def _letter_choice_number(text: str) -> str | None:
    match = _LETTER_CHOICE_PATTERN.match(text)
    return _LETTER_CHOICES[match.group(1)] if match else None


# 객관식: 다른 숫자에 붙어 있지 않은 맨 앞의 1~5 한 자리 ("정답: 3", "3번", "(3) 광합성")
_FIRST_CHOICE_DIGIT = re.compile(r"(?<!\d)([1-5])(?!\d)")

# 주관식으로 분류됐어도 형태가 명백히 선택지 번호인 경우만 숫자로 정규화한다.
# "1945년", "2차 세계대전", "3 kg" 같은 단답은 건드리지 않는다.
_EXPLICIT_CHOICE_PATTERNS = (
    re.compile(r"^[(\[]?([1-5])[)\]]?\.?$"),  # "3", "(3)", "3."
    re.compile(r"^[(\[]?([1-5])[)\]]?\s*번"),  # "3번", "3번 광합성"
    re.compile(r"^(?:정답|답|answer)\s*(?:은|는|[:：])?\s*[(\[]?([1-5])(?!\d)", re.IGNORECASE),
)


def normalize_result(raw: str, answer_type: AnswerType) -> str:
    """객관식 정답을 숫자 한 자리('1'~'5')로 정규화한다(알파벳·한글 기호도 숫자로).
    해당 없으면 원문을 정리해 반환."""
    text = " ".join(raw.translate(_CIRCLED_DIGITS).split())
    if not text:
        return "인식 불가"

    if answer_type == "choice":
        match = _FIRST_CHOICE_DIGIT.search(text)
        if match:
            return match.group(1)
        return _letter_choice_number(text) or text

    for pattern in _EXPLICIT_CHOICE_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(1)
    # 주관식으로 분류됐어도 선택지 기호 단독("B", "(B)", "B번")이면 숫자로 바꾼다.
    if re.fullmatch(rf"[(\[]?[{_LETTER_CLASS}][)\].]?\s*번?", text):
        return _letter_choice_number(text) or text
    return text


class AnalysisResult(BaseModel):
    answer_type: AnswerType = Field(description="객관식이면 choice, 그 외는 text")
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
                answer_type = data.get("answer_type")
                return AnalysisResult(
                    answer_type=answer_type if answer_type in ("choice", "text") else "text",
                    result=str(data.get("result", "")).strip(),
                    summary=str(data.get("summary", "")).strip(),
                )
            except ValidationError:
                continue

    raise AnalyzerError("JSON 파싱 실패")


# Google API 오류 응답의 ErrorInfo.reason → 메뉴바에 표시할 짧은 메시지
_ERROR_REASONS = {
    "API_KEY_INVALID": "API 키 오류",
    "ACCESS_TOKEN_TYPE_UNSUPPORTED": "키 유형 거부(AQ)",
    "API_KEY_SERVICE_BLOCKED": "키에 Gemini 미허용",
    "SERVICE_DISABLED": "Gemini API 비활성",
    "RATE_LIMIT_EXCEEDED": "사용량 초과(429)",
}


def error_reason(exc: errors.APIError) -> str | None:
    """오류 응답 details 에서 ErrorInfo.reason 값을 찾는다."""
    details = exc.details if isinstance(exc.details, dict) else {}
    error = details.get("error", details)
    for item in error.get("details", []) if isinstance(error, dict) else []:
        if isinstance(item, dict) and item.get("reason"):
            return str(item["reason"])
    return None


def _api_error_message(exc: errors.APIError) -> str:
    reason = error_reason(exc)
    if reason in _ERROR_REASONS:
        return _ERROR_REASONS[reason]
    if exc.code == 429:
        return "사용량 초과(429)"
    if exc.code in (400, 401, 403) and "API key" in (exc.message or ""):
        return "API 키 오류"
    return f"API 오류 {exc.code}"


class Analyzer:
    def __init__(self, api_key: str, model: str | None = None) -> None:
        if not api_key:
            raise AnalyzerError("API 키 없음")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        # GUI/Keychain에서 읽은 키를 명시적으로 전달한다.
        self._client = genai.Client(api_key=api_key)
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AnalysisResult,
            temperature=0.2,
            max_output_tokens=ANSWER_MAX_TOKENS + THINKING_BUDGET,
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
        )

    def verify(self) -> None:
        """키와 모델이 유효한지 가벼운 메타데이터 조회로 확인한다. 실패 시 AnalyzerError."""
        try:
            self._client.models.get(model=self.model)
        except errors.APIError as exc:
            raise AnalyzerError(_api_error_message(exc)) from exc

    def analyze(self, png_bytes: bytes) -> AnalysisResult:
        image = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[image, USER_PROMPT],
                config=self._config,
            )
        except errors.APIError as exc:
            raise AnalyzerError(_api_error_message(exc)) from exc

        parsed = response.parsed
        if isinstance(parsed, AnalysisResult):
            result = parsed
        elif isinstance(parsed, dict):
            result = _parse_json_text(json.dumps(parsed, ensure_ascii=False))
        else:
            result = _parse_json_text(response.text)

        result.result = normalize_result(result.result, result.answer_type)
        result.summary = " ".join(result.summary.split())
        return result
