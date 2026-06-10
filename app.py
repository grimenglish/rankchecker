
import re
import csv
import json
import time
import random
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


APP_TITLE = "식혜명가 쇼핑 순위 체크"
HISTORY_PATH = Path("rank_history.csv")


# -----------------------------
# 기본 유틸
# -----------------------------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def norm(text: str) -> str:
    return "".join(str(text or "").lower().split())


def includes_any(text: str, terms: List[str]) -> bool:
    ntext = norm(text)
    return any(norm(term) in ntext for term in terms if term.strip())


def is_ad_text(text: str) -> bool:
    t = norm(text)
    return "광고" in t or "sponsored" in t or "adbadge" in t


def save_history(rows: List[Dict]) -> None:
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


def make_session() -> requests.Session:
    # 쿠키/로그인 정보 없는 새 세션. 브라우저 시크릿모드와 비슷한 기준으로 체크.
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
    })
    return s


def fetch_html(session: requests.Session, url: str, referer: str = "") -> str:
    headers = {}
    if referer:
        headers["Referer"] = referer

    r = session.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


# -----------------------------
# 검색 URL
# -----------------------------
def coupang_search_url(keyword: str, page_num: int) -> str:
    q = urllib.parse.quote(keyword)
    return f"https://www.coupang.com/np/search?q={q}&page={page_num}&listSize=36"


def naver_search_url(keyword: str, page_num: int) -> str:
    q = urllib.parse.quote(keyword)
    return (
        "https://search.shopping.naver.com/search/all"
        f"?query={q}&pagingIndex={page_num}&pagingSize=40&sort=rel"
    )


# -----------------------------
# JSON 내부 상품 후보 추출
# -----------------------------
def walk_json(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_json(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_json(item)


def get_first_value(d: Dict, keys: List[str]) -> str:
    for k in keys:
        if k in d and d[k]:
            return str(d[k])
    # 일부 사이트는 대소문자/스네이크가 섞일 수 있음
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        lk = k.lower()
        if lk in lower and lower[lk]:
            return str(lower[lk])
    return ""


def extract_json_cards_from_scripts(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    cards = []
    seen = set()

    scripts = soup.find_all("script")
    for script in scripts:
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue

        json_candidates = []

        if script.get("id") == "__NEXT_DATA__":
            json_candidates.append(raw)

        # 아주 큰 script 중 JSON처럼 생긴 경우만 제한적으로 시도
        if raw.startswith("{") and raw.endswith("}"):
            json_candidates.append(raw)

        for candidate in json_candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue

            for d in walk_json(data):
                title = get_first_value(d, [
                    "productName", "productTitle", "itemName", "name", "title",
                    "product_name", "item_name"
                ])
                url = get_first_value(d, [
                    "productUrl", "mallProductUrl", "crUrl", "url", "linkUrl",
                    "link", "mobileUrl"
                ])
                mall = get_first_value(d, ["mallName", "mall", "storeName", "channelName"])
                price = get_first_value(d, ["price", "salePrice", "lowPrice", "productPrice"])

                text = " ".join([title, mall, price, json.dumps(d, ensure_ascii=False)[:500]])

                if not title or len(norm(title)) < 3:
                    continue

                # 메뉴/카테고리류 잡음 제거
                bad_title = ["네이버쇼핑", "검색결과", "카테고리", "로그인", "장바구니"]
                if any(x in title for x in bad_title):
                    continue

                full_url = urljoin(base_url, url) if url else ""
                key = full_url or norm(title)[:80]
                if not key or key in seen:
                    continue
                seen.add(key)

                cards.append({
                    "title": title,
                    "text": text,
                    "url": full_url,
                    "ad": is_ad_text(text),
                })

    return cards


# -----------------------------
# 쿠팡 파싱
# -----------------------------
def extract_coupang_cards(html: str, base_url: str, exclude_ads: bool) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    seen = set()

    selectors = [
        "li.search-product",
        "li[class*='search-product']",
        "ul#productList li",
    ]

    elements = []
    for selector in selectors:
        found = soup.select(selector)
        if len(found) >= 3:
            elements = found
            break

    for el in elements:
        text = el.get_text(" ", strip=True)
        a = el.select_one("a[href]")
        href = urljoin(base_url, a.get("href", "")) if a else ""

        title_el = (
            el.select_one(".name")
            or el.select_one("[class*='name']")
            or el.select_one("a[href]")
        )
        title = title_el.get_text(" ", strip=True) if title_el else text[:80]

        key = href or norm(title)
        if not key or key in seen:
            continue
        seen.add(key)

        ad = is_ad_text(text) or bool(el.select_one("[class*='ad']"))
        if exclude_ads and ad:
            continue

        if title or href:
            cards.append({
                "title": title,
                "text": text,
                "url": href,
                "ad": ad,
            })

    # DOM에서 못 찾으면 JSON 후보도 확인
    if not cards:
        cards = extract_json_cards_from_scripts(soup, base_url)

    return cards


# -----------------------------
# 네이버 파싱
# -----------------------------
def extract_naver_cards(html: str, base_url: str, exclude_ads: bool) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    seen = set()

    # 1차: Next.js JSON에서 상품 정보 추출
    json_cards = extract_json_cards_from_scripts(soup, base_url)
    for card in json_cards:
        key = card["url"] or norm(card["title"])
        if key and key not in seen:
            seen.add(key)
            if not (exclude_ads and card["ad"]):
                cards.append(card)

    # 2차: HTML 카드 추출
    selectors = [
        "div[class*='product_item']",
        "li[class*='basicList_item']",
        "div[class*='basicList_item']",
        "div[class*='adProduct_item']",
    ]

    elements = []
    for selector in selectors:
        found = soup.select(selector)
        if len(found) >= 3:
            elements = found
            break

    for el in elements:
        text = el.get_text(" ", strip=True)
        links = el.select("a[href]")

        href = ""
        title = ""
        for a in links:
            h = a.get("href", "")
            t = a.get_text(" ", strip=True)
            if not title and len(norm(t)) >= 3:
                title = t
            if any(domain in h for domain in ["shopping.naver.com", "smartstore.naver.com", "brand.naver.com", "adcr.naver.com"]):
                href = urljoin(base_url, h)
                if not title:
                    title = t
                break

        if not title:
            title = text[:80]

        key = href or norm(title)
        if not key or key in seen:
            continue
        seen.add(key)

        ad = is_ad_text(text) or "adProduct" in str(el.get("class", ""))
        if exclude_ads and ad:
            continue

        if len(norm(title)) >= 3:
            cards.append({
                "title": title,
                "text": text,
                "url": href,
                "ad": ad,
            })

    return cards


# -----------------------------
# 순위 체크
# -----------------------------
def check_one(platform: str, keyword: str, match_terms: List[str], max_pages: int, exclude_ads: bool) -> Dict:
    rank = 0
    scanned = 0
    session = make_session()

    for page_num in range(1, max_pages + 1):
        url = coupang_search_url(keyword, page_num) if platform == "쿠팡" else naver_search_url(keyword, page_num)

        try:
            html = fetch_html(session, url)
        except Exception as e:
            return {
                "체크시간": now_str(),
                "플랫폼": platform,
                "검색어": keyword,
                "순위": "",
                "상태": f"접속 실패: {e}",
                "발견페이지": page_num,
                "상품명": "",
                "상품URL": "",
                "확인상품수": scanned,
            }

        cards = (
            extract_coupang_cards(html, url, exclude_ads)
            if platform == "쿠팡"
            else extract_naver_cards(html, url, exclude_ads)
        )

        if page_num == 1 and not cards:
            # 서버 차단/구조 변경 가능성
            page_hint = "상품 목록 추출 실패"
        else:
            page_hint = ""

        for card in cards:
            rank += 1
            scanned += 1
            target = f"{card.get('title','')} {card.get('text','')} {card.get('url','')}"
            if includes_any(target, match_terms):
                return {
                    "체크시간": now_str(),
                    "플랫폼": platform,
                    "검색어": keyword,
                    "순위": rank,
                    "상태": "발견",
                    "발견페이지": page_num,
                    "상품명": card.get("title", ""),
                    "상품URL": card.get("url", ""),
                    "확인상품수": scanned,
                }

        time.sleep(random.uniform(0.8, 1.8))

    status = f"{max_pages}페이지 내 미발견"
    if scanned == 0:
        status = "상품 목록 추출 실패 또는 사이트 차단 가능"

    return {
        "체크시간": now_str(),
        "플랫폼": platform,
        "검색어": keyword,
        "순위": "",
        "상태": status,
        "발견페이지": "",
        "상품명": "",
        "상품URL": "",
        "확인상품수": scanned,
    }


def run_check(platforms: List[str], keywords: List[str], match_terms: List[str], max_pages: int, exclude_ads: bool) -> List[Dict]:
    rows = []
    total = len(platforms) * len(keywords)
    done = 0
    bar = st.progress(0)

    for platform in platforms:
        for keyword in keywords:
            done += 1
            st.write(f"체크 중: **{platform} / {keyword}**")
            rows.append(check_one(platform, keyword, match_terms, max_pages, exclude_ads))
            bar.progress(done / total)

    return rows


# -----------------------------
# Streamlit 화면
# -----------------------------
st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")

st.title("📈 식혜명가 쇼핑 순위 체크")
st.caption("네이버 쇼핑 / 쿠팡 검색 결과에서 내 상품이 몇 위인지 확인합니다.")

st.success("이 버전은 Playwright/브라우저 설치 없이 실행됩니다.")

with st.sidebar:
    st.header("설정")
    platforms = st.multiselect("체크할 플랫폼", ["네이버", "쿠팡"], default=["네이버", "쿠팡"])
    max_pages = st.slider("최대 확인 페이지", 1, 10, 5)
    exclude_ads = st.checkbox("광고 상품 제외", value=True)

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

st.info("식별어는 `식혜명가`, 상품명 핵심 단어, 스마트스토어 주소 일부, 쿠팡 상품 URL 일부를 넣으면 됩니다.")

if st.button("순위 체크 시작", type="primary", use_container_width=True):
    keywords = clean_lines(keywords_text)
    match_terms = clean_lines(match_text)

    if not platforms:
        st.error("플랫폼을 선택하세요.")
    elif not keywords:
        st.error("검색어를 입력하세요.")
    elif not match_terms:
        st.error("내 상품 식별어를 입력하세요.")
    else:
        with st.spinner("순위 체크 중입니다."):
            try:
                rows = run_check(platforms, keywords, match_terms, max_pages, exclude_ads)
                df = pd.DataFrame(rows)
                save_history(rows)

                st.subheader("체크 결과")
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

            except Exception as e:
                st.error("실행 오류")
                st.code(str(e))

st.divider()
st.subheader("누적 기록")

history = load_history()
if history.empty:
    st.caption("아직 기록이 없습니다.")
else:
    st.dataframe(history.tail(200), use_container_width=True)

    chart_df = history.copy()
    if "순위" in chart_df.columns:
        chart_df["순위"] = pd.to_numeric(chart_df["순위"], errors="coerce")
        chart_df = chart_df.dropna(subset=["순위"])

        if not chart_df.empty:
            st.caption("순위는 숫자가 낮을수록 좋습니다.")
            for platform in chart_df["플랫폼"].dropna().unique():
                for keyword in chart_df["검색어"].dropna().unique():
                    sub = chart_df[(chart_df["플랫폼"] == platform) & (chart_df["검색어"] == keyword)]
                    if len(sub) >= 2:
                        st.write(f"**{platform} / {keyword} 순위 추이**")
                        st.line_chart(sub.set_index("체크시간")["순위"])
