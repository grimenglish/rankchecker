
import os
import re
import sys
import time
import random
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st


APP_VERSION = "v2.1-fixed"
APP_TITLE = "Product Rank Tracker"
HISTORY_PATH = Path("rank_history.csv")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_lines(text: str) -> List[str]:
    return [x.strip() for x in text.splitlines() if x.strip()]


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    return (
        text.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def norm(text: str) -> str:
    return "".join(str(text or "").lower().split())


def includes_any(text: str, terms: List[str]) -> bool:
    t = norm(text)
    return any(norm(term) in t for term in terms if term.strip())


def save_history(rows: List[Dict]):
    if not rows:
        return
    df = pd.DataFrame(rows)
    exists = HISTORY_PATH.exists()
    df.to_csv(HISTORY_PATH, mode="a", header=not exists, index=False, encoding="utf-8-sig")


def load_history() -> pd.DataFrame:
    if HISTORY_PATH.exists():
        try:
            return pd.read_csv(HISTORY_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def clear_history():
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()


def is_streamlit_cloud() -> bool:
    return bool(os.environ.get("STREAMLIT_SHARING") or os.environ.get("STREAMLIT_CLOUD"))


# =============================
# 네이버 공식 API
# =============================
def naver_api_search(client_id: str, client_secret: str, keyword: str, start: int, display: int = 100) -> Dict:
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": client_id.strip(),
        "X-Naver-Client-Secret": client_secret.strip(),
    }
    params = {
        "query": keyword,
        "display": display,
        "start": start,
        "sort": "sim",
    }

    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def check_naver_api(client_id: str, client_secret: str, keyword: str, match_terms: List[str], max_rank: int) -> Dict:
    checked = 0

    for start in range(1, max_rank + 1, 100):
        try:
            data = naver_api_search(client_id, client_secret, keyword, start=start, display=100)
        except Exception as e:
            return {
                "체크시간": now_str(),
                "플랫폼": "네이버",
                "검색어": keyword,
                "순위": "",
                "상태": f"API 오류: {e}",
                "발견페이지": "",
                "상품명": "",
                "상품URL": "",
                "몰명": "",
                "확인상품수": checked,
            }

        items = data.get("items", [])

        for i, item in enumerate(items, start=start):
            if i > max_rank:
                break

            checked += 1

            title = strip_html(item.get("title", ""))
            link = item.get("link", "")
            mall = item.get("mallName", "")
            maker = item.get("maker", "")
            brand = item.get("brand", "")
            category = " ".join([
                item.get("category1", ""),
                item.get("category2", ""),
                item.get("category3", ""),
                item.get("category4", ""),
            ])

            target = f"{title} {link} {mall} {maker} {brand} {category}"

            if includes_any(target, match_terms):
                page_num = ((i - 1) // 40) + 1
                return {
                    "체크시간": now_str(),
                    "플랫폼": "네이버",
                    "검색어": keyword,
                    "순위": i,
                    "상태": "발견",
                    "발견페이지": page_num,
                    "상품명": title,
                    "상품URL": link,
                    "몰명": mall,
                    "확인상품수": checked,
                }

        if len(items) < 100:
            break

    return {
        "체크시간": now_str(),
        "플랫폼": "네이버",
        "검색어": keyword,
        "순위": "",
        "상태": f"{max_rank}위 내 미발견",
        "발견페이지": "",
        "상품명": "",
        "상품URL": "",
        "몰명": "",
        "확인상품수": checked,
    }


# =============================
# 쿠팡 브라우저 방식
# =============================
def ensure_playwright_ready():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            browser.close()
        return True, "브라우저 준비 완료"
    except Exception as e:
        err = str(e)
        if "Executable doesn't exist" in err or "playwright install" in err:
            try:
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return True, "브라우저 자동 설치 완료"
            except Exception as install_error:
                return False, f"브라우저 설치 실패: {install_error}"
        return False, err


def coupang_search_url(keyword: str, page_num: int) -> str:
    q = urllib.parse.quote(keyword)
    return f"https://www.coupang.com/np/search?q={q}&page={page_num}&listSize=36"


def safe_text(el) -> str:
    try:
        if el is None:
            return ""
        return el.inner_text(timeout=1000).strip()
    except Exception:
        return ""


def safe_hrefs(el, base_url: str) -> List[str]:
    try:
        hrefs = el.eval_on_selector_all(
            "a[href]",
            "(els) => els.map(a => a.href || a.getAttribute('href') || '').filter(Boolean)"
        )
        return [urljoin(base_url, h) for h in hrefs if h]
    except Exception:
        return []


def is_ad_text(text: str) -> bool:
    t = norm(text)
    return "광고" in t or "sponsored" in t


def extract_coupang_cards(page, exclude_ads: bool) -> List[Dict]:
    selectors = [
        "li.search-product",
        "li[class*='search-product']",
        "ul#productList li",
    ]

    elements = []
    for selector in selectors:
        try:
            found = page.query_selector_all(selector)
            if len(found) >= 3:
                elements = found
                break
        except Exception:
            pass

    cards = []
    seen = set()

    for el in elements:
        text = safe_text(el)
        hrefs = safe_hrefs(el, page.url)

        product_url = ""
        for h in hrefs:
            if "coupang.com" in h and ("/vp/products/" in h or "/np/products/" in h):
                product_url = h
                break

        title = ""
        try:
            title_el = el.query_selector(".name") or el.query_selector("[class*='name']") or el.query_selector("a[href]")
            title = safe_text(title_el) if title_el else ""
        except Exception:
            pass

        if not title:
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            title = lines[0] if lines else ""

        key = product_url or norm(title)
        if not key or key in seen:
            continue
        seen.add(key)

        ad = is_ad_text(text)
        if exclude_ads and ad:
            continue

        if title or product_url:
            cards.append({
                "title": title,
                "text": text,
                "url": product_url,
                "ad": ad,
            })

    return cards


def check_coupang_browser(keyword: str, match_terms: List[str], max_pages: int, exclude_ads: bool, headless: bool) -> Dict:
    ok, msg = ensure_playwright_ready()
    if not ok:
        return {
            "체크시간": now_str(),
            "플랫폼": "쿠팡",
            "검색어": keyword,
            "순위": "",
            "상태": f"브라우저 준비 실패: {msg}",
            "발견페이지": "",
            "상품명": "",
            "상품URL": "",
            "몰명": "",
            "확인상품수": 0,
        }

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except Exception as e:
        return {
            "체크시간": now_str(),
            "플랫폼": "쿠팡",
            "검색어": keyword,
            "순위": "",
            "상태": f"Playwright 오류: {e}",
            "발견페이지": "",
            "상품명": "",
            "상품URL": "",
            "몰명": "",
            "확인상품수": 0,
        }

    rank = 0
    scanned = 0

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = browser.new_context(
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1365, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            page = context.new_page()

            for page_num in range(1, max_pages + 1):
                url = coupang_search_url(keyword, page_num)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeoutError:
                        pass
                    page.wait_for_timeout(2000)
                except Exception as e:
                    context.close()
                    browser.close()
                    return {
                        "체크시간": now_str(),
                        "플랫폼": "쿠팡",
                        "검색어": keyword,
                        "순위": "",
                        "상태": f"접속 실패: {e}",
                        "발견페이지": page_num,
                        "상품명": "",
                        "상품URL": "",
                        "몰명": "",
                        "확인상품수": scanned,
                    }

                cards = extract_coupang_cards(page, exclude_ads)

                for card in cards:
                    rank += 1
                    scanned += 1

                    target = f"{card.get('title','')} {card.get('text','')} {card.get('url','')}"
                    if includes_any(target, match_terms):
                        context.close()
                        browser.close()
                        return {
                            "체크시간": now_str(),
                            "플랫폼": "쿠팡",
                            "검색어": keyword,
                            "순위": rank,
                            "상태": "발견",
                            "발견페이지": page_num,
                            "상품명": card.get("title", ""),
                            "상품URL": card.get("url", ""),
                            "몰명": "쿠팡",
                            "확인상품수": scanned,
                        }

                time.sleep(random.uniform(1.3, 2.8))

            context.close()
            browser.close()

    except Exception as e:
        return {
            "체크시간": now_str(),
            "플랫폼": "쿠팡",
            "검색어": keyword,
            "순위": "",
            "상태": f"실행 오류: {e}",
            "발견페이지": "",
            "상품명": "",
            "상품URL": "",
            "몰명": "",
            "확인상품수": scanned,
        }

    status = f"{max_pages}페이지 내 미발견"
    if scanned == 0:
        status = "상품 목록 추출 실패 또는 쿠팡 차단 가능"

    return {
        "체크시간": now_str(),
        "플랫폼": "쿠팡",
        "검색어": keyword,
        "순위": "",
        "상태": status,
        "발견페이지": "",
        "상품명": "",
        "상품URL": "",
        "몰명": "",
        "확인상품수": scanned,
    }


# =============================
# Streamlit 화면
# =============================
st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")

st.title("📈 Product Rank Tracker")
st.caption(f"네이버 쇼핑 + 쿠팡 상품 순위 체크 / {APP_VERSION}")

cloud = is_streamlit_cloud()
if cloud:
    st.warning("Streamlit Cloud에서는 쿠팡이 차단될 수 있습니다. 쿠팡까지 안정적으로 쓰려면 PC 실행이 가장 좋습니다.")
else:
    st.success("로컬 PC 실행 환경입니다. 네이버 + 쿠팡 동시 체크에 적합합니다.")

with st.sidebar:
    st.header("체크 설정")

    platforms = st.multiselect(
        "체크할 플랫폼",
        ["네이버", "쿠팡"],
        default=["네이버", "쿠팡"],
    )

    st.divider()
    st.subheader("네이버 API")
    client_id = st.text_input("Naver Client ID", type="password")
    client_secret = st.text_input("Naver Client Secret", type="password")
    naver_max_rank = st.slider("네이버 최대 확인 순위", 100, 1000, 500, step=100)

    st.divider()
    st.subheader("쿠팡")
    coupang_max_pages = st.slider("쿠팡 최대 확인 페이지", 1, 10, 5)
    exclude_ads = st.checkbox("광고 상품 제외", value=True)
    headless = st.checkbox("쿠팡 브라우저 숨기고 실행", value=True)

    st.divider()
    st.subheader("기록 관리")
    if st.button("누적 기록 초기화"):
        clear_history()
        st.success("누적 기록을 삭제했습니다.")
        st.rerun()

col1, col2 = st.columns(2)

with col1:
    keywords_text = st.text_area(
        "검색어 목록",
        value="식혜\n수제식혜\n전통식혜\n단호박식혜",
        height=160,
    )

with col2:
    match_text = st.text_area(
        "내 상품 식별어",
        value="식혜명가",
        height=160,
        help="상품명, 브랜드명, 스마트스토어 주소 일부, 쿠팡 상품 URL 일부",
    )

st.info("식별어 예시: `식혜명가`, `상품명 핵심 단어`, `smartstore 주소 일부`, `쿠팡 상품 URL 일부`")

if st.button("네이버 + 쿠팡 순위 체크 시작", type="primary", use_container_width=True):
    keywords = clean_lines(keywords_text)
    match_terms = clean_lines(match_text)

    if not platforms:
        st.error("플랫폼을 선택하세요.")
    elif not keywords:
        st.error("검색어를 입력하세요.")
    elif not match_terms:
        st.error("내 상품 식별어를 입력하세요.")
    elif "네이버" in platforms and (not client_id or not client_secret):
        st.error("네이버를 체크하려면 Naver Client ID와 Client Secret을 입력하세요.")
    else:
        rows = []
        total_jobs = len(platforms) * len(keywords)
        done = 0
        bar = st.progress(0)

        with st.spinner("순위 체크 중입니다."):
            for keyword in keywords:
                if "네이버" in platforms:
                    st.write(f"체크 중: **네이버 / {keyword}**")
                    rows.append(check_naver_api(client_id, client_secret, keyword, match_terms, naver_max_rank))
                    done += 1
                    bar.progress(done / total_jobs)

                if "쿠팡" in platforms:
                    st.write(f"체크 중: **쿠팡 / {keyword}**")
                    rows.append(check_coupang_browser(keyword, match_terms, coupang_max_pages, exclude_ads, headless))
                    done += 1
                    bar.progress(done / total_jobs)

        df = pd.DataFrame(rows)
        save_history(rows)

        st.subheader("이번 체크 결과")
        st.dataframe(df, use_container_width=True)

        csv_data = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "CSV 다운로드",
            data=csv_data,
            file_name=f"rank_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        excel_path = Path(f"rank_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        df.to_excel(excel_path, index=False)

        st.download_button(
            "엑셀 다운로드",
            data=excel_path.read_bytes(),
            file_name=excel_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

st.divider()
st.subheader("누적 기록")

history = load_history()
if history.empty:
    st.caption("아직 기록이 없습니다.")
else:
    st.caption("예전 버전에서 발생한 418/403 오류 기록도 여기에 남아있을 수 있습니다. 필요하면 왼쪽에서 초기화하세요.")
    st.dataframe(history.tail(300), use_container_width=True)

    chart_df = history.copy()
    if "순위" in chart_df.columns:
        chart_df["순위"] = pd.to_numeric(chart_df["순위"], errors="coerce")
        chart_df = chart_df.dropna(subset=["순위"])

        if not chart_df.empty:
            st.caption("순위는 숫자가 낮을수록 좋습니다.")
            for platform in chart_df["플랫폼"].dropna().unique():
                for keyword in chart_df["검색어"].dropna().unique():
                    sub = chart_df[
                        (chart_df["플랫폼"] == platform)
                        & (chart_df["검색어"] == keyword)
                    ]
                    if len(sub) >= 2:
                        st.write(f"**{platform} / {keyword} 순위 추이**")
                        st.line_chart(sub.set_index("체크시간")["순위"])

st.divider()
with st.expander("PC에서 실행하는 방법"):
    st.code("pip install -r requirements.txt", language="bash")
    st.code("python -m playwright install chromium", language="bash")
    st.code("streamlit run app.py", language="bash")
