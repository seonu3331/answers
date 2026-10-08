"""Gemini API 키 저장/로드.

저장 우선순위:
  1. macOS Keychain (서비스명 rubric_gemini) — 기본값, 가장 안전
  2. ~/.rubric_gemini/config.json (권한 0600) — Keychain 사용이 불가능할 때의 대체 저장소

로드 우선순위:
  Keychain → config.json → 환경 변수(GEMINI_API_KEY / GOOGLE_API_KEY, .env 포함)

Keychain 접근은 macOS 기본 제공 `security` 명령을 사용한다. 키 값을 명령행 인자로
넘기면 `ps`로 노출될 수 있으므로, `security -i`(대화형 모드)에 표준 입력으로 전달한다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

KEYCHAIN_SERVICE = "rubric_gemini"
KEYCHAIN_ACCOUNT = "GEMINI_API_KEY"

CONFIG_DIR = Path.home() / ".rubric_gemini"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Google API 키는 영숫자와 '-', '_'로 구성된다. 공백/따옴표가 섞이지 않음을 보장해
# security -i 명령 문자열에 안전하게 넣을 수 있게 한다.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")


class StoredKey(NamedTuple):
    key: str
    source: str  # "Keychain" | "config.json" | "환경 변수"


def normalize_key(raw: str) -> str:
    """입력값을 정리하고 형식을 검사한다. 형식이 틀리면 ValueError."""
    key = raw.strip().strip('"').strip("'").strip()
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError("API 키 형식이 올바르지 않습니다. (영문/숫자/-/_ 20자 이상)")
    return key


def mask_key(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "****"


# ---------- Keychain ----------


def _keychain_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def _keychain_get() -> str | None:
    if not _keychain_available():
        return None
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _keychain_set(key: str) -> bool:
    if not _keychain_available():
        return False
    command = (
        f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} "
        f"-l {KEYCHAIN_SERVICE} -w {key}\n"
    )
    subprocess.run(
        ["security", "-i"],
        input=command,
        capture_output=True,
        text=True,
        check=False,
    )
    # 대화형 모드는 개별 명령 실패에도 0을 반환할 수 있으므로 다시 읽어 검증한다.
    return _keychain_get() == key


def _keychain_delete() -> None:
    if _keychain_available():
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
            capture_output=True,
            check=False,
        )


# ---------- config.json ----------


def _read_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(data: dict) -> None:
    """임시 파일에 0600 권한으로 쓴 뒤 원자적으로 교체한다."""
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, CONFIG_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------- 공개 API ----------


def load_api_key() -> StoredKey | None:
    key = _keychain_get()
    if key:
        return StoredKey(key, "Keychain")

    key = str(_read_config().get("api_key", "")).strip()
    if key:
        return StoredKey(key, "config.json")

    key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if key:
        return StoredKey(key, "환경 변수")
    return None


def save_api_key(raw: str) -> StoredKey:
    """키를 검사한 뒤 Keychain(우선) 또는 config.json에 저장하고, 저장된 위치를 반환한다."""
    key = normalize_key(raw)
    config = _read_config()

    if _keychain_set(key):
        # Keychain 저장에 성공하면 평문 사본이 남지 않도록 config.json의 키를 지운다.
        config.pop("api_key", None)
        config["storage"] = "keychain"
        _write_config(config)
        return StoredKey(key, "Keychain")

    config["api_key"] = key
    config["storage"] = "config.json"
    _write_config(config)
    return StoredKey(key, "config.json")


def delete_api_key() -> None:
    _keychain_delete()
    config = _read_config()
    if "api_key" in config or "storage" in config:
        config.pop("api_key", None)
        config.pop("storage", None)
        _write_config(config)
