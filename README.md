# ScreenAnswer — 커서 기반 화면 분석 메뉴바 유틸리티 (macOS)

`Ctrl + Option + Cmd`를 눌렀다 떼면 마우스 커서 주변 800×600(pt) 영역을 캡처해
Gemini(`gemini-2.5-flash`)로 분석하고, 결과를 메뉴바 타이틀에 `[결과값]` 형태로 표시합니다.

## 설치 및 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # GEMINI_API_KEY 입력
python main.py
```

## macOS 권한 (최초 1회)

시스템 설정 > 개인정보 보호 및 보안에서 실행 주체(터미널, iTerm, VS Code 등)를 허용하세요.

| 권한 | 용도 |
| --- | --- |
| 손쉬운 사용 / 입력 모니터링 | pynput 전역 단축키 감지 |
| 화면 기록 | mss 화면 캡처 (미허용 시 바탕화면만 찍힘) |

권한 변경 후에는 터미널 앱을 재시작해야 적용됩니다.

## 사용법

- 메뉴바 `[ · ]` 대기 → `[ … ]` 분석 중 → `[3]` 결과
- 드롭다운: 결과/요약 확인(클릭 시 클립보드 복사), `초기화(Clear)`, `종료(Quit)`
- 단축키를 누른 채 다른 키를 함께 누르면(다른 앱 단축키) 트리거되지 않습니다.

## 모듈 구조

| 파일 | 역할 |
| --- | --- |
| `main.py` | rumps 메뉴바 앱, 상태 관리, 작업 스레드 → 메인 스레드 UI 큐 |
| `hotkey.py` | pynput 수정키 조합 감지, 커서 좌표 조회 |
| `capture.py` | 커서 중심 캡처, 모니터 경계 보정, Retina 배율 계산, 커서 마커 표시 |
| `analyzer.py` | google-genai SDK 호출, JSON 스키마 강제 및 파싱 |

## 참고

- 스크린샷에는 커서가 찍히지 않으므로, 커서 위치에 빨간 원/십자 마커를 그려 모델에 전달합니다.
- Retina: 요청 영역(pt) 대비 실제 캡처 픽셀 수로 배율을 계산해 마커 위치를 보정합니다.
  최신 mss는 macOS에서 논리 해상도(1x)로 캡처하며, 2x로 캡처되는 환경에서도 그대로 동작합니다.
- 모델은 `.env`의 `GEMINI_MODEL`로 바꿀 수 있습니다.
