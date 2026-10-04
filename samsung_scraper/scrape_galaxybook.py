"""
삼성닷컴(한국) 갤럭시 북 전체 제품 수집기

1) 목록 페이지(all-galaxybook)에서 모든 제품 링크를 모은다 ("더보기" 자동 클릭 + 스크롤)
2) 각 제품 상세 페이지에 들어가 스펙 표 전체(RAM 포함)를 수집한다
3) 사용자 필터(프로세서 / RAM / 화면 크기)에 맞는 제품을 표시한다
4) 결과를 CSV / XLSX / JSON으로 저장한다

사용법:
    pip install -r requirements.txt
    python -m playwright install chromium
    python scrape_galaxybook.py            # 브라우저 창을 띄워서 실행 (차단 회피에 유리)
    python scrape_galaxybook.py --headless # 창 없이 실행
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://www.samsung.com"
LIST_URL = (
    "https://www.samsung.com/sec/galaxybook/all-galaxybook/"
    "?Ultra%20X7+Ultra%205+Ultra%207+32GB+16GB+40.6cm+39.6cm"
)

# ---- 필터 조건 (원하는 대로 수정) ----
FILTER_CPU = ["Ultra X7", "Ultra 5", "Ultra 7"]
FILTER_RAM_GB = [16, 32]
FILTER_SCREEN_CM = ["40.6", "39.6"]

MODEL_RE = re.compile(r"\b(N[TP]\d{3}[A-Z0-9]{2,6}-[A-Z0-9]{3,8})\b")
OUT_DIR = Path(__file__).parent / "output"
DEBUG_DIR = OUT_DIR / "debug"


# --------------------------------------------------------------------------
# 목록 페이지
# --------------------------------------------------------------------------
def close_popups(page):
    for text in ["동의", "확인", "닫기", "Accept", "오늘 하루 보지 않기"]:
        try:
            btn = page.get_by_role("button", name=text, exact=False).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1000)
        except Exception:
            pass


def load_all_items(page, max_rounds=60):
    """'더보기' 버튼을 더 이상 없을 때까지 누르고 끝까지 스크롤한다."""
    last_count = -1
    stable = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(800)
        clicked = False
        for sel in [
            "button:has-text('더보기')",
            "a:has-text('더보기'):not([href*='http'])",
            "button:has-text('View more')",
            "[class*='more'] button",
        ]:
            try:
                btn = page.locator(sel).last
                if btn.is_visible(timeout=300):
                    btn.scroll_into_view_if_needed(timeout=1000)
                    btn.click(timeout=2000)
                    page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                continue
        count = page.locator("a[href*='/sec/']").count()
        if count == last_count and not clicked:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last_count = count


def collect_list(page):
    """목록 카드에서 상세 링크 + 카드 텍스트를 수집. 모델코드 기준 중복 제거."""
    raw = page.evaluate(
        """() => {
        const out = [];
        for (const a of document.querySelectorAll('a[href]')) {
            const href = a.getAttribute('href') || '';
            if (!href.includes('/sec/')) continue;
            // 카드 단위 컨테이너 텍스트 (상위로 올라가며 적당한 크기의 블록)
            let card = a;
            for (let i = 0; i < 6 && card.parentElement; i++) {
                card = card.parentElement;
                if ((card.innerText || '').length > 60) break;
            }
            out.push({
                href,
                text: (a.innerText || '').trim(),
                card_text: (card.innerText || '').trim().slice(0, 1500),
            });
        }
        return out;
    }"""
    )
    items = {}
    for r in raw:
        href = r["href"]
        m = MODEL_RE.search(href) or MODEL_RE.search(r["card_text"])
        if not m:
            continue
        # 제품 상세/구매 페이지로 보이는 링크만
        if not re.search(r"/sec/(galaxybook|pc|.*mobile|hiddenair)", href) and "modelCode" not in href:
            continue
        code = m.group(1)
        url = urljoin(BASE, href)
        prev = items.get(code)
        # 모델코드가 URL에 직접 들어간 링크를 우선
        if prev is None or (code not in prev["url"] and code in url):
            items[code] = {
                "model_code": code,
                "url": url,
                "list_card_text": r["card_text"],
            }
    return list(items.values())


# --------------------------------------------------------------------------
# 상세 페이지
# --------------------------------------------------------------------------
EXTRACT_JS = r"""() => {
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const specs = {};
    const put = (k, v) => {
        k = clean(k); v = clean(v);
        if (!k || !v || k.length > 60 || k === v) return;
        if (specs[k] && specs[k] !== v && !specs[k].includes(v)) specs[k] += ' | ' + v;
        else if (!specs[k]) specs[k] = v;
    };
    // 1) dl/dt/dd
    document.querySelectorAll('dl').forEach(dl => {
        const dts = dl.querySelectorAll(':scope > dt, :scope > div > dt');
        dts.forEach(dt => {
            let dd = dt.nextElementSibling;
            while (dd && dd.tagName !== 'DD') dd = dd.nextElementSibling;
            if (dd) put(dt.innerText, dd.innerText);
        });
    });
    // 2) table th/td
    document.querySelectorAll('table tr').forEach(tr => {
        const th = tr.querySelector('th'); const td = tr.querySelector('td');
        if (th && td) put(th.innerText, td.innerText);
    });
    // 3) class 이름에 spec 이 들어간 key/value 구조
    document.querySelectorAll('[class*="spec" i] li, [class*="spec" i] [class*="item" i]').forEach(li => {
        const k = li.querySelector('[class*="tit" i], [class*="name" i], [class*="key" i], [class*="label" i], strong, h4, h5');
        const v = li.querySelector('[class*="desc" i], [class*="value" i], [class*="cont" i], [class*="txt" i], p, span:not(:first-child)');
        if (k && v && k !== v) put(k.innerText, v.innerText);
    });
    // 스펙 영역 원문 (파서가 놓친 정보 보존)
    const specAreas = [...document.querySelectorAll('[class*="spec" i], [id*="spec" i]')]
        .map(e => e.innerText || '').filter(t => t.length > 50);
    specAreas.sort((a, b) => b.length - a.length);

    const title = clean(
        (document.querySelector('h1') || {}).innerText ||
        (document.querySelector('meta[property="og:title"]') || {}).content || document.title);
    const priceEls = [...document.querySelectorAll('[class*="price" i]')]
        .map(e => clean(e.innerText)).filter(t => /\d[\d,]{3,}\s*원/.test(t));
    const ld = [...document.querySelectorAll('script[type="application/ld+json"]')]
        .map(s => s.textContent);
    return {
        title,
        prices: [...new Set(priceEls)].slice(0, 5),
        specs,
        spec_raw: (specAreas[0] || '').slice(0, 20000),
        page_text: clean(document.body.innerText).slice(0, 30000),
        json_ld: ld,
    };
}"""


def expand_specs(page):
    """'스펙 전체보기' 류 버튼/탭을 눌러 숨은 스펙을 펼친다 (링크 이동은 하지 않음)."""
    for label in ["전체 스펙", "스펙 전체", "스펙 더보기", "사양 더보기", "스펙", "사양", "제품 사양", "Specs"]:
        try:
            loc = page.locator(f"button:has-text('{label}'), [role='tab']:has-text('{label}'), "
                               f"a[href^='#']:has-text('{label}')")
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                if el.is_visible(timeout=300):
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(timeout=1500)
                    page.wait_for_timeout(700)
        except Exception:
            continue
    # 지연 로딩되는 영역을 위해 끝까지 스크롤
    for _ in range(12):
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(250)


def scrape_detail(page, item):
    page.goto(item["url"], wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    close_popups(page)
    expand_specs(page)
    data = page.evaluate(EXTRACT_JS)
    item.update(data)
    item["final_url"] = page.url
    return item


# --------------------------------------------------------------------------
# 정규화 / 필터
# --------------------------------------------------------------------------
def find_spec(specs, *keys):
    for k, v in specs.items():
        if any(key.lower() in k.lower() for key in keys):
            return v
    return ""


def normalize(item):
    specs = item.get("specs", {})
    text = " ".join([item.get("title", ""), item.get("list_card_text", ""),
                     item.get("spec_raw", ""), item.get("page_text", "")[:15000]])

    cpu = find_spec(specs, "프로세서", "CPU", "Processor")
    if not cpu:
        m = re.search(r"Core\S*\s*Ultra\s*X?\d[\w ]{0,12}", text)
        cpu = m.group(0).strip() if m else ""

    ram = find_spec(specs, "메모리", "RAM", "Memory")
    ram_gb = None
    m = re.search(r"(\d{1,3})\s*GB", ram) if ram else None
    if not m:  # 스펙표에서 못 찾으면 본문에서 'LPDDR' 근처 용량 탐색
        m = (re.search(r"(\d{1,3})\s*GB\s*(?:LP)?DDR", text)
             or re.search(r"(?:메모리|RAM)[^\d]{0,20}(\d{1,3})\s*GB", text))
    if m:
        ram_gb = int(m.group(1))

    screen = find_spec(specs, "화면 크기", "디스플레이 크기", "화면크기", "Screen Size")
    m = re.search(r"(\d{2}\.\d)\s*cm", screen or "") or re.search(r"(\d{2}\.\d)\s*cm", text)
    screen_cm = m.group(1) if m else ""

    storage = find_spec(specs, "저장", "SSD", "Storage")
    if not storage:
        m = re.search(r"(\d+\s*(?:TB|GB))\s*NVMe", text)
        storage = m.group(1) if m else ""

    item.update({
        "cpu": cpu,
        "ram_gb": ram_gb,
        "ram_raw": ram,
        "screen_cm": screen_cm,
        "storage": storage,
        "gpu": find_spec(specs, "그래픽", "GPU", "Graphics"),
        "display": find_spec(specs, "해상도", "디스플레이", "Resolution"),
        "weight": find_spec(specs, "무게", "Weight"),
        "battery": find_spec(specs, "배터리", "Battery"),
        "os": find_spec(specs, "운영체제", "OS"),
        "color": find_spec(specs, "색상", "Color"),
        "price": (item.get("prices") or [""])[0],
    })

    cpu_ok = any(re.search(rf"Ultra\s*{re.escape(c.split()[-1])}\b", cpu + " " + item.get("title", ""), re.I)
                 for c in FILTER_CPU)
    ram_ok = ram_gb in FILTER_RAM_GB
    scr_ok = screen_cm in FILTER_SCREEN_CM
    item["filter_match"] = cpu_ok and ram_ok and scr_ok
    return item


# --------------------------------------------------------------------------
# 저장
# --------------------------------------------------------------------------
SUMMARY_COLS = ["filter_match", "model_code", "title", "price", "cpu", "ram_gb", "screen_cm",
                "storage", "gpu", "display", "weight", "battery", "os", "color", "final_url"]


def save(items):
    OUT_DIR.mkdir(exist_ok=True)
    all_spec_keys = []
    for it in items:
        for k in it.get("specs", {}):
            if k not in all_spec_keys:
                all_spec_keys.append(k)

    rows = []
    for it in items:
        row = {c: it.get(c, "") for c in SUMMARY_COLS}
        for k in all_spec_keys:
            row["[스펙] " + k] = it.get("specs", {}).get(k, "")
        rows.append(row)
    cols = SUMMARY_COLS + ["[스펙] " + k for k in all_spec_keys]

    with open(OUT_DIR / "galaxybook_all.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with open(OUT_DIR / "galaxybook_filtered.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows([r for r in rows if r["filter_match"]])
    with open(OUT_DIR / "galaxybook_all.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    try:
        from openpyxl import Workbook
        wb = Workbook()
        for name, subset in [("필터 일치", [r for r in rows if r["filter_match"]]), ("전체", rows)]:
            ws = wb.active if name == "필터 일치" else wb.create_sheet(name)
            ws.title = name
            ws.append(cols)
            for r in subset:
                ws.append([str(r[c]) if r[c] is not None else "" for c in cols])
            ws.freeze_panes = "C2"
        wb.save(OUT_DIR / "galaxybook.xlsx")
    except ImportError:
        print("openpyxl 미설치 → xlsx 생략 (CSV는 엑셀에서 바로 열 수 있음)")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    ap.add_argument("--limit", type=int, default=0, help="상세 페이지 최대 개수 (테스트용)")
    ap.add_argument("--list-url", default=LIST_URL)
    args = ap.parse_args()

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context(
            locale="ko-KR",
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()

        # 목록 API 응답(JSON)을 debug 폴더에 저장 → 구조가 바뀌었을 때 확인용
        def on_response(resp):
            try:
                if "json" in (resp.headers.get("content-type") or "") and "/sec/" in resp.url:
                    name = re.sub(r"[^A-Za-z0-9]+", "_", resp.url.split("?")[0])[-80:]
                    (DEBUG_DIR / f"{int(time.time()*1000)}_{name}.json").write_text(
                        resp.text(), encoding="utf-8")
            except Exception:
                pass
        page.on("response", on_response)

        print(f"[1/3] 목록 페이지 로딩: {args.list_url}")
        page.goto(args.list_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass
        close_popups(page)
        load_all_items(page)
        page.screenshot(path=str(DEBUG_DIR / "list_page.png"), full_page=True)
        (DEBUG_DIR / "list_page.html").write_text(page.content(), encoding="utf-8")
        page.remove_listener("response", on_response)

        items = collect_list(page)
        print(f"      → 제품 {len(items)}개 발견")
        if not items:
            print("제품 링크를 찾지 못했습니다. output/debug/list_page.png / .html 을 확인해 주세요.")
            sys.exit(1)
        if args.limit:
            items = items[: args.limit]

        print("[2/3] 상세 페이지 수집")
        for i, it in enumerate(items, 1):
            try:
                scrape_detail(page, it)
                normalize(it)
                print(f"  ({i}/{len(items)}) {it['model_code']} | {it['cpu'][:30]} | "
                      f"RAM {it['ram_gb']}GB | {it['screen_cm']}cm | {'✔' if it['filter_match'] else ' '}")
            except Exception as e:
                it["error"] = str(e)
                print(f"  ({i}/{len(items)}) {it['model_code']} 실패: {e}")
            time.sleep(1.0)  # 서버 부담 완화

        browser.close()

    print("[3/3] 저장")
    save(items)
    n = sum(1 for it in items if it.get("filter_match"))
    print(f"완료: 전체 {len(items)}개 / 필터 일치 {n}개 → {OUT_DIR}")


if __name__ == "__main__":
    main()
