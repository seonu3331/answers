# ScreenAnswer — 커서 기반 화면 분석 메뉴바 앱 (macOS)

`Ctrl + Option + Cmd`를 눌렀다 떼면 마우스 커서 주변(화면 가로 2/3 × 세로 90%)을 캡처해
Gemini(`gemini-2.5-flash`)로 분석하고, 결과를 메뉴바에 짧게 표시합니다.
`Ctrl + Option + Shift + Cmd`로는 5초마다 자동으로 분석하는 모드를 켜고 끕니다.

| 상태 | 메뉴바 표시 |
| --- | --- |
| 대기 | `·` |
| 분석 중 | `..` |
| 결과 | `3` (객관식은 숫자 1개, 주관식은 1~3단어) → **5초 후 자동으로 `·` 복귀** |
| 자동 분석 시작 직후 | `↻` → 이후 결과가 5초마다 갱신(복귀 없이 유지) |
| 오류 | `⚠ API 키 오류` 등 → 5초 후 복귀 |

AlDente처럼 **Dock 아이콘과 메뉴바가 함께 보이는 일반 앱**입니다(`LSUIElement = False`).
그래서 시스템 설정의 권한 목록에서 `ScreenAnswer`를 쉽게 찾을 수 있습니다.

> 대상 환경: **Apple Silicon Mac (M1 이상) + macOS 26**

## 1. 설치 방법 A — 빌드된 DMG 받기 (가장 쉬움)

GitHub Actions가 macOS 26 Apple Silicon 머신에서 앱을 빌드해 `.dmg`로 올려 둡니다.

1. 저장소의 **Releases**(태그 `v*` 빌드) 또는 **Actions → Build macOS app → 최신 성공 실행 → Artifacts**에서
   `ScreenAnswer-arm64`를 받습니다. Artifacts는 zip으로 받아지므로 압축을 풀면 `.dmg`가 나옵니다.
2. `.dmg`를 더블 클릭해 열고, `ScreenAnswer`를 옆의 `Applications` 폴더로 드래그합니다.
3. **처음 열 때 차단 해제** (Apple 공증을 받지 않은 앱이라 한 번은 필요합니다)
   - `응용 프로그램`에서 ScreenAnswer를 더블 클릭 → "Apple이 확인할 수 없습니다" 경고가 뜨면 **완료**를 누릅니다.
   - **시스템 설정 → 개인정보 보호 및 보안** 맨 아래의 "ScreenAnswer이(가) 차단되었습니다" 옆 **그래도 열기**를 누르고 암호를 입력합니다.
   - 또는 터미널에서 한 줄로 해결할 수 있습니다.
     ```bash
     xattr -dr com.apple.quarantine /Applications/ScreenAnswer.app
     ```
4. 이후 단계는 아래 **3. API 키 설정**, **4. macOS 권한**을 따르세요.

## 2. 설치 방법 B — 소스를 받아 직접 빌드

### 2-1. 준비물 (최초 1회)

```bash
xcode-select --install          # Git, 컴파일 도구 (이미 있으면 "already installed" 메시지)
```

Python 3.12를 설치합니다. 둘 중 하나를 고르세요.
- [python.org](https://www.python.org/downloads/macos/)의 macOS 64-bit universal2 설치 파일 (**권장**)
- Homebrew: `brew install python@3.12`

macOS 기본 `/usr/bin/python3`(3.9)는 너무 오래돼서 쓰면 안 됩니다. 버전을 확인하세요.
```bash
python3.12 --version            # Python 3.12.x
```

### 2-2. 소스 받기

비공개 저장소이므로 GitHub에 로그인한 상태에서 받아야 합니다.

- **웹으로 받기**: 저장소 페이지 → 초록색 **Code** 버튼 → **Download ZIP** → 압축 해제
- **git으로 받기**: `git clone https://github.com/seonu3331/answers.git`
  (암호 대신 [Personal Access Token](https://github.com/settings/tokens)을 입력하거나 `gh auth login` 사용)

### 2-3. 빌드 및 설치

```bash
cd ~/Downloads/answers                         # 압축을 푼(또는 clone한) 폴더
chmod +x build_app.sh make_dmg.sh              # ZIP으로 받으면 실행 권한이 빠질 수 있음
PYTHON=python3.12 ./build_app.sh --install     # 빌드 → /Applications/ScreenAnswer.app
open /Applications/ScreenAnswer.app
```

- 빌드는 3~5분 걸립니다. 직접 빌드한 앱은 차단 해제(방법 A의 3단계)가 필요 없습니다.
- 배포용 `.dmg`가 필요하면 `./make_dmg.sh`를 실행하세요 → `dist/ScreenAnswer-<버전>-arm64.dmg`
- 개발 중 빠른 확인: `./build_app.sh --alias` (소스를 참조하는 번들)
- 실행 로그: `~/Library/Logs/ScreenAnswer.log`

### 2-4. (개발용) 터미널에서 바로 실행

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

이 경우 Dock 아이콘과 권한 목록에는 앱 대신 Python/터미널이 표시되고, 권한도 터미널 앱에 줘야 합니다.

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
| 입력 모니터링 | 전역 단축키 감지 (Quartz 이벤트 탭) |
| 손쉬운 사용 | (권장) 일부 macOS 버전에서 단축키 감지에 필요 |
| 화면 기록 | 화면 캡처 (mss). 없으면 바탕화면만 찍힘 |

권한을 바꾼 뒤에는 앱을 다시 시작하세요(메뉴 `종료(Quit)` 후 다시 실행).
화면 기록 권한이 없으면 메뉴바에 `⚠ 화면 기록 권한 필요`가 표시됩니다.

캡처가 이상하면 진단 도구로 확인하세요. 결과 이미지는 바탕화면에 저장됩니다.
```bash
.venv/bin/python scripts/capture_check.py
```
**다시 빌드하면 서명이 바뀌어 권한이 풀릴 수 있습니다.** 이때는 목록에서 `ScreenAnswer`를 `−`로 지우고 다시 추가하세요.

## 5. 사용법

| 동작 | 방법 |
| --- | --- |
| 1회 분석 | `⌃ Control + ⌥ Option + ⌘ Command` 를 함께 눌렀다 떼기 |
| 5초 자동 분석 켜기/끄기 | `⌃ Control + ⌥ Option + ⇧ Shift + ⌘ Command` 를 함께 눌렀다 떼기, 또는 메뉴 **5초 자동 분석 시작/중지** |

- 캡처 영역: 커서가 있는 모니터의 **가로 2/3 × 세로 90%** (MacBook Pro 14" 기본 해상도 기준 약 1008×884pt),
  커서를 중심으로 잡고 화면 밖으로 나가면 안쪽으로 밀어 넣습니다.
- 자동 분석은 매 회차마다 **그 순간의 커서 위치**를 기준으로 캡처합니다. 이전 분석이 끝나지 않았으면 끝난 뒤 실행합니다.
- 자동 분석은 분당 약 12회 API를 호출합니다. 무료 등급 한도를 넘으면 `⚠ 사용량 초과(429)`가 뜨고 30초 쉬었다가 재개합니다.
- 조합 중 다른 키를 함께 누르면(다른 앱 단축키) 실행되지 않습니다.
- 드롭다운에서 마지막 결과·요약을 다시 볼 수 있고, 클릭하면 클립보드에 복사됩니다.

### 단축키가 반응하지 않을 때

1. 메뉴의 **단축키 상태** 줄을 확인합니다.
   - `정상`이면 단축키가 등록된 상태입니다. 세 키를 **동시에 누른 뒤 뗄 때** 실행됩니다.
   - `⚠ 입력 모니터링 권한 필요`면 그 줄을 클릭 → 설정에서 ScreenAnswer를 켭니다.
2. 설정에서 켜져 있는데도 `권한 필요`가 뜨면, **이전 버전 앱 기준으로 등록된 권한**이 남아 있는 상태입니다.
   (이 앱은 Apple 개발자 인증서 없이 임시 서명되어, macOS가 빌드마다 다른 앱으로 인식합니다)
   메뉴 **권한 설정 열기 → 권한 초기화 후 재시작…** 을 누르면 이 앱의 권한 항목만 지우고 재시작합니다.
   재시작 후 화면 기록·입력 모니터링을 새로 허용하고, 앱을 한 번 더 종료 후 실행하세요.
   터미널로 직접 할 수도 있습니다:
   ```bash
   for s in ScreenCapture ListenEvent Accessibility; do tccutil reset $s com.rubric.screenanswer; done
   ```
   **앱을 업데이트할 때마다 한 번씩** 필요합니다. 앱 실행 시 이 상태를 감지하면 안내 창이 뜹니다.
3. 그래도 안 되면 메뉴 **권한 설정 열기 → 진단 정보 복사** 결과와 `~/Library/Logs/ScreenAnswer.log`를 확인합니다.

## 모듈 구조

| 파일 | 역할 |
| --- | --- |
| `main.py` | rumps 메뉴바 앱, 키 입력 창, 상태 표시, 5초 자동 복귀 |
| `config.py` | API 키 저장/로드 (Keychain → `~/.rubric_gemini/config.json`) |
| `hotkey.py` | Quartz 이벤트 탭으로 수정키 조합 감지(1회/자동), 커서 좌표 조회 |
| `capture.py` | 커서 중심 캡처(ScreenCaptureKit 우선, mss 대체), 모니터 경계 보정, Retina 배율 계산, 커서 마커 표시 |
| `analyzer.py` | google-genai 호출, JSON 스키마 강제, 객관식 번호 정규화 |
| `setup.py` / `build_app.sh` / `make_icon.py` / `make_dmg.sh` | py2app 빌드 설정, 빌드 스크립트, 아이콘 생성, DMG 패키징 |
| `scripts/capture_check.py` | 캡처 진단 (두 백엔드 비교) |
| `.github/workflows/build-macos.yml` | macOS 26 Apple Silicon 자동 빌드 → DMG 아티팩트 / 릴리스 |

## 객관식 번호 정규화

모델이 `answer_type`을 `choice`(객관식) 또는 `text`(그 외)로 함께 반환합니다.

- `choice`: 다른 숫자에 붙어 있지 않은 **맨 앞의 숫자 1~5 하나**만 남깁니다.
  `3번`, `정답: 3`, `정답은 ④번`, `(2) 광합성` → `3`, `3`, `4`, `2`
- `text`: `3번`, `정답: 5`처럼 형태가 분명한 선택지 번호만 숫자로 바꿉니다.
  `1945년`, `2차 세계대전` 같은 단답은 그대로 둡니다.
