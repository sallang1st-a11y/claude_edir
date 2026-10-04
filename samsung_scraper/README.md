# 삼성닷컴 갤럭시 북 수집기

목록 페이지(`all-galaxybook`)에서 모든 제품을 찾은 뒤 **각 상세 페이지에 들어가** 스펙 표 전체(RAM 포함)를 수집합니다.
필터(Core Ultra X7 / Ultra 5 / Ultra 7, RAM 16GB / 32GB, 40.6cm / 39.6cm)에 맞는 제품은 `filter_match = True`로 표시합니다.

## 실행 (Windows / Mac 공통)

```bash
cd samsung_scraper
pip install -r requirements.txt
python -m playwright install chromium
python scrape_galaxybook.py              # 브라우저 창이 뜬 상태로 실행 (권장)
python scrape_galaxybook.py --limit 3    # 3개만 시험 실행
python scrape_galaxybook.py --headless   # 창 없이 실행
```

## 결과 (`output/` 폴더)

| 파일 | 내용 |
|---|---|
| `galaxybook.xlsx` | 시트1 "필터 일치", 시트2 "전체" |
| `galaxybook_filtered.csv` | 필터에 맞는 제품만 (엑셀에서 바로 열림) |
| `galaxybook_all.csv` | 수집한 전체 제품 |
| `galaxybook_all.json` | 원본 데이터 (스펙 원문, 페이지 텍스트 포함) |
| `debug/` | 목록 페이지 스크린샷·HTML, 사이트 API 응답(JSON) |

요약 열: 모델코드, 제품명, 가격, CPU, RAM(GB), 화면(cm), 저장장치, GPU, 디스플레이, 무게, 배터리, OS, 색상, URL.
그 뒤에는 상세 페이지 스펙 표의 모든 항목이 `[스펙] 항목명` 열로 붙습니다.

필터 조건은 `scrape_galaxybook.py` 위쪽의 `FILTER_CPU`, `FILTER_RAM_GB`, `FILTER_SCREEN_CM`에서 바꿀 수 있습니다.

## 문제가 생기면

- **제품이 0개로 나올 때**: `output/debug/list_page.png`를 보고 페이지가 제대로 열렸는지 확인하세요. 접속이 차단된 경우 `--headless` 없이 실행하세요.
- **RAM이나 CPU 칸이 비어 있을 때**: `galaxybook_all.json`의 `spec_raw`/`page_text`에는 원문이 들어 있습니다. 이 파일이나 `debug/` 폴더를 공유해 주시면 파서를 맞춰 드립니다.
