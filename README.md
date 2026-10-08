# ScreenAnswer — 커서 기반 화면 분석 메뉴바 앱 (macOS)

`Ctrl + Option + Cmd`를 눌렀다 떼면 마우스 커서 주변 800×600(pt) 영역을 캡처해
Gemini(`gemini-2.5-flash`)로 분석하고, 결과를 메뉴바에 짧게 표시합니다.

| 상태 | 메뉴바 표시 |
| --- | --- |
| 대기 | `·` |
| 분석 중 | `..` |
| 결과 | `3` (객관식은 숫자 1개, 주관식은 1~3단어) → **5초 후 자동으로 `·` 복귀** |
| 오류 | `⚠ API 키 오류` 등 → 5초 후 복귀 |

AlDente처럼 **Dock 아이콘과 메뉴바가 함께 보이는 일반 앱**입니다(`LSUIElement = False`).
그래서 시스템 설정의 권한 목록에서 `ScreenAnswer`를 쉽게 찾을 수 있습니다.

## 1. .app 빌드 (권장)

```bash
./build_app.sh             # dist/ScreenAnswer.app 생성
./build_app.sh --install   # 빌드 후 /Applications 에 복사
open dist/ScreenAnswer.app # 또는 Finder에서 더블 클릭
```

빌드 스크립트가 하는 일: `.venv` 생성 → 의존성과 py2app 설치 → 아이콘(`assets/ScreenAnswer.icns`) 생성
→ `python setup.py py2app` → ad-hoc 코드 서명(`codesign --sign -`).

- 개발 중 빠른 확인: `./build_app.sh --alias` (소스를 참조하는 번들, 다른 Mac에 배포 불가)
- 다른 Python을 쓰려면: `PYTHON=/opt/homebrew/bin/python3.12 ./build_app.sh`
  (python.org 또는 Homebrew Python 권장. pyenv는 `--enable-framework`/`--enable-shared` 빌드가 필요)
- 실행 로그: `~/Library/Logs/ScreenAnswer.log`

## 2. 소스에서 바로 실행 (개발용)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

이 경우 Dock 아이콘과 권한 목록에는 앱 대신 Python/터미널이 표시됩니다.

## 3. API 키 설정

- 처음 실행할 때 키가 없으면 **입력 창이 자동으로 뜹니다**. 입력값은 가려서 표시되고 붙여넣기(⌘V)가 됩니다.
- 이후에는 메뉴바 `·` → **API Key 설정…** 에서 언제든 바꿀 수 있습니다.
- 저장하면 키가 유효한지 백그라운드에서 확인하고, 결과는 메뉴의 `API Key: AIza…abcd (Keychain) · 확인됨` 줄에 표시됩니다.

저장 위치:

1. **macOS Keychain** (서비스 `rubric_gemini`) — 기본값
2. `~/.rubric_gemini/config.json` (권한 0600) — Keychain을 쓸 수 없을 때만 사용

불러오는 순서는 Keychain → config.json → 환경 변수 `GEMINI_API_KEY`(개발용 `.env` 포함)입니다.
키를 지우려면 `키체인 접근` 앱에서 `rubric_gemini` 항목을 삭제하거나
`security delete-generic-password -s rubric_gemini -a GEMINI_API_KEY` 를 실행하세요.

## 4. macOS 권한 (최초 1회)

메뉴바 `·` → **권한 설정 열기**에서 해당 설정 화면으로 바로 갈 수 있습니다.

| 권한 | 용도 |
| --- | --- |
| 손쉬운 사용 / 입력 모니터링 | 전역 단축키 감지 (pynput) |
| 화면 기록 | 화면 캡처 (mss). 없으면 바탕화면만 찍힘 |

권한을 바꾼 뒤에는 앱을 다시 시작하세요.
**다시 빌드하면 서명이 바뀌어 권한이 풀릴 수 있습니다.** 이때는 목록에서 `ScreenAnswer`를 `−`로 지우고 다시 추가하세요.

## 5. 사용법

- `Ctrl + Option + Cmd`를 함께 눌렀다 뗍니다. 다른 키와 섞어 누르면(다른 앱 단축키) 실행되지 않습니다.
- 드롭다운에서 마지막 결과와 요약을 다시 볼 수 있고, 클릭하면 클립보드에 복사됩니다.
- `초기화(Clear)`는 결과를 지우고, `종료(Quit)`는 앱을 끕니다.

## 모듈 구조

| 파일 | 역할 |
| --- | --- |
| `main.py` | rumps 메뉴바 앱, 키 입력 창, 상태 표시, 5초 자동 복귀 |
| `config.py` | API 키 저장/로드 (Keychain → `~/.rubric_gemini/config.json`) |
| `hotkey.py` | pynput 수정키 조합 감지, 커서 좌표 조회 |
| `capture.py` | 커서 중심 캡처, 모니터 경계 보정, Retina 배율 계산, 커서 마커 표시 |
| `analyzer.py` | google-genai 호출, JSON 스키마 강제, 객관식 번호 정규화 |
| `setup.py` / `build_app.sh` / `make_icon.py` | py2app 빌드 설정, 빌드 스크립트, 아이콘 생성 |

## 객관식 번호 정규화

모델이 `answer_type`을 `choice`(객관식) 또는 `text`(그 외)로 함께 반환합니다.

- `choice`: 다른 숫자에 붙어 있지 않은 **맨 앞의 숫자 1~5 하나**만 남깁니다.
  `3번`, `정답: 3`, `정답은 ④번`, `(2) 광합성` → `3`, `3`, `4`, `2`
- `text`: `3번`, `정답: 5`처럼 형태가 분명한 선택지 번호만 숫자로 바꿉니다.
  `1945년`, `2차 세계대전` 같은 단답은 그대로 둡니다.
