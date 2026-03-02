from __future__ import annotations

from io import BytesIO
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None
from openpyxl import Workbook
import requests
from flask import Flask, render_template, request, send_file

app = Flask(__name__)
TIMEOUT_SECONDS = int(os.getenv("FEED_READ_TIMEOUT", "20"))


@dataclass(frozen=True)
class FeedSource:
    key: str
    language: str
    name: str
    feed_url: str
    homepage: str
    fallback_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()


ALL_SOURCES_KEY = "all_sources"
ALL_LANGUAGES_KEY = "all_languages"

LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    (ALL_LANGUAGES_KEY, "전체"),
    ("한국어", "한국어"),
    ("English", "English"),
    ("日本語", "日本語"),
)


SOURCES: dict[str, FeedSource] = {
    "kr_korea_policy": FeedSource(
        key="kr_korea_policy",
        language="한국어",
        name="정책브리핑 (대한민국 정부)",
        feed_url="https://www.korea.kr/rss/policy.xml",
        homepage="https://www.korea.kr/news/policyNewsList.do",
        sitemap_urls=("https://www.korea.kr/sitemap.xml",),
    ),
    "jp_nhk_news": FeedSource(
        key="jp_nhk_news",
        language="日本語",
        name="NHK NEWS (Public Broadcaster)",
        feed_url="https://www3.nhk.or.jp/rss/news/cat0.xml",
        homepage="https://www3.nhk.or.jp/news/",
        sitemap_urls=("https://www3.nhk.or.jp/sitemap.xml",),
    ),
    "jp_meti_news": FeedSource(
        key="jp_meti_news",
        language="日本語",
        name="経済産業省 ニュースリリース (불안정 가능)",
        feed_url="https://www.meti.go.jp/rss/news_release.xml",
        homepage="https://www.meti.go.jp/english/press/index.html",
        fallback_urls=(
            "https://meti.go.jp/rss/news_release.xml",
        ),
        sitemap_urls=("https://www.meti.go.jp/sitemap.xml",),
    ),
    "jp_mainichi_news": FeedSource(
        key="jp_mainichi_news",
        language="日本語",
        name="毎日新聞 速報RSS",
        feed_url="https://mainichi.jp/rss/etc/mainichi-flash.rss",
        homepage="https://mainichi.jp/",
    ),
    "jp_yahoo_news": FeedSource(
        key="jp_yahoo_news",
        language="日本語",
        name="Yahoo!ニュース 主要RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_nhk_society": FeedSource(
        key="jp_nhk_society",
        language="日本語",
        name="NHK NEWS 社会RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat1.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_world": FeedSource(
        key="jp_nhk_world",
        language="日本語",
        name="NHK NEWS 国際RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat6.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_politics": FeedSource(
        key="jp_nhk_politics",
        language="日本語",
        name="NHK NEWS 政治RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat4.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_economy": FeedSource(
        key="jp_nhk_economy",
        language="日本語",
        name="NHK NEWS 経済RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat5.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_science": FeedSource(
        key="jp_nhk_science",
        language="日本語",
        name="NHK NEWS 科学・文化RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat7.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "en_nasa_news": FeedSource(
        key="en_nasa_news",
        language="English",
        name="NASA News Releases (U.S. Government)",
        feed_url="https://www.nasa.gov/news-release/feed/",
        homepage="https://www.nasa.gov/news/all-news/",
        sitemap_urls=("https://www.nasa.gov/sitemap_index.xml", "https://www.nasa.gov/sitemap.xml"),
    ),
    "en_bbc_world": FeedSource(
        key="en_bbc_world",
        language="English",
        name="BBC World News RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/rss.xml",
        homepage="https://www.bbc.com/news/world",
    ),
    "en_nyt_world": FeedSource(
        key="en_nyt_world",
        language="English",
        name="NYTimes World RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        homepage="https://www.nytimes.com/section/world",
    ),
    "en_guardian_world": FeedSource(
        key="en_guardian_world",
        language="English",
        name="The Guardian World RSS",
        feed_url="https://www.theguardian.com/world/rss",
        homepage="https://www.theguardian.com/world",
    ),
    "en_npr_world": FeedSource(
        key="en_npr_world",
        language="English",
        name="NPR World RSS",
        feed_url="https://feeds.npr.org/1004/rss.xml",
        homepage="https://www.npr.org/sections/world/",
    ),
    "en_bbc_business": FeedSource(
        key="en_bbc_business",
        language="English",
        name="BBC Business RSS",
        feed_url="https://feeds.bbci.co.uk/news/business/rss.xml",
        homepage="https://www.bbc.com/news/business",
    ),
    "en_bbc_tech": FeedSource(
        key="en_bbc_tech",
        language="English",
        name="BBC Technology RSS",
        feed_url="https://feeds.bbci.co.uk/news/technology/rss.xml",
        homepage="https://www.bbc.com/news/technology",
    ),
    "en_bbc_science": FeedSource(
        key="en_bbc_science",
        language="English",
        name="BBC Science RSS",
        feed_url="https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        homepage="https://www.bbc.com/news/science_and_environment",
    ),
    "kr_yna_news": FeedSource(
        key="kr_yna_news",
        language="한국어",
        name="연합뉴스 RSS",
        feed_url="https://www.yna.co.kr/rss/news.xml",
        homepage="https://www.yna.co.kr/",
    ),
    "kr_kbs_news": FeedSource(
        key="kr_kbs_news",
        language="한국어",
        name="KBS 뉴스 RSS",
        feed_url="https://news.kbs.co.kr/rss/rss.xml",
        homepage="https://news.kbs.co.kr/",
    ),
    "kr_mbc_news": FeedSource(
        key="kr_mbc_news",
        language="한국어",
        name="MBC 뉴스 RSS",
        feed_url="https://imnews.imbc.com/rss/news/news_00.xml",
        homepage="https://imnews.imbc.com/",
    ),
    "kr_sbs_news": FeedSource(
        key="kr_sbs_news",
        language="한국어",
        name="SBS 뉴스 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01",
        homepage="https://news.sbs.co.kr/",
    ),
    "kr_sbs_politics": FeedSource(
        key="kr_sbs_politics",
        language="한국어",
        name="SBS 정치 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=02",
    ),
    "kr_sbs_economy": FeedSource(
        key="kr_sbs_economy",
        language="한국어",
        name="SBS 경제 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=03",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=03",
    ),
    "kr_sbs_world": FeedSource(
        key="kr_sbs_world",
        language="한국어",
        name="SBS 국제 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=04",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=04",
    ),
}

ARTICLE_SELECTORS: dict[str, tuple[str, ...]] = {
    "kr_korea_policy": ("#newsView p", ".article_txt p", ".view_cont p", "article p"),
    "kr_yna_news": ("#articleWrap p", ".story-news p", "article p"),
    "kr_kbs_news": ("#cont_newstext p", ".detail-body p", "article p"),
    "kr_mbc_news": ("#newsContent p", ".news_end p", "article p"),
    "kr_sbs_news": (".main_text p", "#container p", "article p"),
    "kr_sbs_politics": (".main_text p", "#container p", "article p"),
    "kr_sbs_economy": (".main_text p", "#container p", "article p"),
    "kr_sbs_world": (".main_text p", "#container p", "article p"),
    "jp_nhk_news": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_meti_news": ("#main p", ".main p", ".container p", "article p"),
    "jp_mainichi_news": (".articledetail-body p", ".article-body p", "article p"),
    "jp_yahoo_news": ("article p", "main p", ".sc-contents p"),
    "jp_nhk_society": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_nhk_world": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_nhk_politics": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_nhk_economy": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_nhk_science": (".content--detail-body p", ".module--article-body p", "article p"),
    "en_nasa_news": (".entry-content p", ".wysiwyg p", "article p"),
    "en_bbc_world": ("[data-component='text-block'] p", "article p", "main p"),
    "en_nyt_world": ("section[name='articleBody'] p", ".StoryBodyCompanionColumn p", "article p"),
    "en_guardian_world": ("#maincontent p", "article p"),
    "en_npr_world": ("#storytext p", ".storytext p", "article p"),
    "en_bbc_business": ("[data-component='text-block'] p", "article p", "main p"),
    "en_bbc_tech": ("[data-component='text-block'] p", "article p", "main p"),
    "en_bbc_science": ("[data-component='text-block'] p", "article p", "main p"),
}


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml_data: bytes | str, limit: int) -> list[dict]:
    # Parse raw XML bytes so parser can honor feed-declared encoding.
    root = ET.fromstring(xml_data)

    rss_items = root.findall("./channel/item")
    if rss_items:
        parsed = []
        for item in rss_items[:limit]:
            title = clean_text(item.findtext("title"))
            link = clean_text(item.findtext("link"))
            published = clean_text(
                item.findtext("pubDate")
                or item.findtext("{http://purl.org/dc/elements/1.1/}date")
            )
            summary = clean_text(item.findtext("description"))
            parsed.append(
                {
                    "title": title,
                    "link": link,
                    "published": published,
                    "summary": summary,
                }
            )
        return parsed

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    atom_entries = root.findall("./atom:entry", namespace)
    parsed = []
    for entry in atom_entries[:limit]:
        title = clean_text(entry.findtext("atom:title", default="", namespaces=namespace))

        link = ""
        link_elem = entry.find("atom:link", namespace)
        if link_elem is not None:
            link = clean_text(link_elem.attrib.get("href", ""))

        published = clean_text(
            entry.findtext("atom:updated", default="", namespaces=namespace)
            or entry.findtext("atom:published", default="", namespaces=namespace)
        )

        summary = clean_text(
            entry.findtext("atom:summary", default="", namespaces=namespace)
            or entry.findtext("atom:content", default="", namespaces=namespace)
        )

        parsed.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
            }
        )
    return parsed


def build_paged_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "paged" in query:
        query["paged"] = str(page_number)
    else:
        query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_sitemap_urls(xml_data: bytes | str) -> list[str]:
    root = ET.fromstring(xml_data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    for loc in root.findall(".//sm:url/sm:loc", ns):
        if loc.text:
            urls.append(clean_text(loc.text))
    return [u for u in urls if u]


def collect_archive_items(source: FeedSource, limit: int) -> list[dict]:
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    candidates: list[str] = []

    for sitemap_url in source.sitemap_urls:
        try:
            res = requests.get(sitemap_url, timeout=(8, TIMEOUT_SECONDS), headers=headers)
            res.raise_for_status()
            urls = parse_sitemap_urls(res.content)
            # Keep only article-like links for each source.
            if source.key == "kr_korea_policy":
                urls = [u for u in urls if "/news/" in u and ".do?" in u]
            elif source.key == "jp_nhk_news":
                urls = [u for u in urls if "/news/html/" in u]
            elif source.key == "jp_meti_news":
                urls = [u for u in urls if "/press/" in u or "/policy/" in u]
            elif source.key == "en_nasa_news":
                urls = [u for u in urls if "/news-release/" in u or "/news/" in u]
            candidates.extend(urls[: limit * 4])
        except (requests.RequestException, ET.ParseError):
            continue

    seen: set[str] = set()
    unique_urls: list[str] = []
    for url in candidates:
        if url and url not in seen:
            seen.add(url)
            unique_urls.append(url)
        if len(unique_urls) >= limit * 3:
            break

    items: list[dict] = []
    for url in unique_urls:
        body = fetch_article_body(source.key, url)
        if not body:
            continue
        items.append(
            {
                "title": "",
                "link": url,
                "published": "",
                "summary": body[:280],
                "body_text": body,
                "source_name": source.name,
                "language": source.language,
                "source_key": source.key,
            }
        )
        if len(items) >= limit:
            break

    return items


def crawl_feed(source: FeedSource, limit: int, history_pages: int = 1) -> list[dict]:
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    urls = (source.feed_url, *source.fallback_urls)
    errors: list[str] = []
    collected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    max_pages = max(1, min(10, history_pages))

    for url in urls:
        for page_number in range(1, max_pages + 1):
            paged_url = build_paged_url(url, page_number)
            page_items: list[dict] = []
            page_ok = False
            for attempt in range(2):
                try:
                    # Use separate connect/read timeout so slow feeds are more tolerant.
                    response = requests.get(
                        paged_url,
                        timeout=(8, TIMEOUT_SECONDS),
                        headers=headers,
                    )
                    response.raise_for_status()
                    page_items = parse_feed(response.content, limit)
                    page_ok = True
                    break
                except (requests.Timeout, requests.ConnectionError) as exc:
                    errors.append(f"{paged_url} ({exc.__class__.__name__})")
                    if attempt == 0:
                        continue
                except (requests.HTTPError, ET.ParseError) as exc:
                    errors.append(f"{paged_url} ({exc.__class__.__name__})")
                    break

            if not page_ok:
                if page_number == 1:
                    break
                continue

            added = 0
            for item in page_items:
                key = (item.get("link", ""), item.get("title", ""))
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                added += 1
                if len(collected) >= limit:
                    return collected[:limit]

            # Stop early when next page does not add new data.
            if added == 0:
                break

        if collected:
            return collected[:limit]

    short_errors = ", ".join(errors[:2]) if errors else "unknown"
    raise requests.RequestException(f"소스 접속 실패(재시도 완료): {short_errors}")


def filter_by_keyword(items: list[dict], keyword: str) -> list[dict]:
    if not keyword:
        return items
    keyword_lower = keyword.lower()
    result = []
    for item in items:
        corpus = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if keyword_lower in corpus:
            result.append(item)
    return result


def extract_article_text(source_key: str, html: bytes | str) -> str:
    if BeautifulSoup is None:
        if isinstance(html, bytes):
            text = html.decode("utf-8", errors="ignore")
        else:
            text = html
        return clean_text(text)[:50000]

    soup = BeautifulSoup(html, "html.parser")
    selectors = ARTICLE_SELECTORS.get(source_key, ("article p", "main p", "p"))

    best_text = ""
    for selector in selectors:
        elements = soup.select(selector)
        if not elements:
            continue

        parts: list[str] = []
        seen: set[str] = set()
        for elem in elements:
            text = clean_text(elem.get_text(" ", strip=True))
            if len(text) < 8:
                continue
            if text in seen:
                continue
            seen.add(text)
            parts.append(text)

        candidate = clean_text(" ".join(parts))
        if len(candidate) > len(best_text):
            best_text = candidate

    if not best_text:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        body = soup.find("body")
        if body:
            best_text = clean_text(body.get_text(" ", strip=True))

    return best_text[:200000]


def fetch_article_body(source_key: str, link: str) -> str:
    if not link:
        return ""
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    try:
        response = requests.get(link, timeout=(8, TIMEOUT_SECONDS), headers=headers)
        response.raise_for_status()
        return extract_article_text(source_key, response.content)
    except requests.RequestException:
        return ""


def enrich_with_article_bodies(items: list[dict], max_items: int | None = None) -> list[dict]:
    if not items:
        return items
    capped = len(items) if max_items is None else min(len(items), max_items)
    for idx in range(capped):
        item = items[idx]
        source_key = item.get("source_key", "")
        link = item.get("link", "")
        body_text = fetch_article_body(source_key, link)
        if body_text:
            item["body_text"] = body_text
    return items


def collect_items(
    selected_source: str,
    selected_language: str,
    keyword: str,
    limit: int,
    history_pages: int = 1,
    parse_article_html: bool = False,
    include_archive: bool = True,
) -> tuple[list[dict], str]:
    # Pull more items before filtering to reduce "too few results" cases.
    fetch_limit = min(200, max(limit, limit * 5 if keyword else limit))
    target_sources = [
        s
        for s in SOURCES.values()
        if selected_language == ALL_LANGUAGES_KEY or s.language == selected_language
    ]

    if selected_source == ALL_SOURCES_KEY:
        if not target_sources:
            return [], "선택한 언어에 해당하는 소스가 없습니다."
        merged: list[dict] = []
        errors: list[str] = []
        for source in target_sources:
            try:
                crawled = crawl_feed(source, fetch_limit, history_pages=history_pages)
                for item in crawled:
                    item["source_name"] = source.name
                    item["language"] = source.language
                    item["source_key"] = source.key
                merged.extend(crawled)
            except requests.RequestException:
                errors.append(source.name)
            except ET.ParseError:
                errors.append(source.name)
            if include_archive:
                merged.extend(collect_archive_items(source, min(fetch_limit, 80)))

        results = filter_by_keyword(merged, keyword)[:limit]
        if results:
            if parse_article_html:
                results = enrich_with_article_bodies(results)
            if errors:
                return results, f"일부 소스 실패: {', '.join(errors[:2])}"
            return results, ""
        if errors:
            return [], f"전체 소스 요청 실패: {', '.join(errors[:3])}"
        return [], "조건에 맞는 결과가 없습니다. 키워드/소스를 바꿔 보세요."

    source = SOURCES.get(selected_source)
    if not source:
        return [], "지원하지 않는 소스입니다."
    if selected_language != ALL_LANGUAGES_KEY and source.language != selected_language:
        return [], "선택한 언어와 뉴스 소스가 일치하지 않습니다."

    try:
        crawled = crawl_feed(source, fetch_limit, history_pages=history_pages)
        results = filter_by_keyword(crawled, keyword)[:limit]
        for item in results:
            item["source_name"] = source.name
            item["language"] = source.language
            item["source_key"] = source.key
        if parse_article_html:
            results = enrich_with_article_bodies(results)
        if include_archive and len(results) < limit:
            archive_items = collect_archive_items(source, min(fetch_limit, 80))
            archive_items = filter_by_keyword(archive_items, keyword)
            results.extend(archive_items)
            dedup: dict[str, dict] = {}
            for item in results:
                dedup[item.get("link", "") or item.get("title", "")] = item
            results = list(dedup.values())[:limit]
        if not results:
            return [], "조건에 맞는 결과가 없습니다. 키워드/소스를 바꿔 보세요."
        return results, ""
    except requests.RequestException as exc:
        return [], f"피드 요청 실패: {exc}"
    except ET.ParseError as exc:
        return [], f"피드 파싱 실패: {exc}"


@app.route("/", methods=["GET", "POST"])
def index():
    source_keys = [ALL_SOURCES_KEY, *list(SOURCES.keys())]
    selected_source = source_keys[0]
    selected_language = ALL_LANGUAGES_KEY
    limit = 12
    keyword = ""
    history_pages = 3
    parse_article_html = False
    include_archive = True
    error = ""
    results: list[dict] = []

    if request.method == "POST":
        selected_source = request.form.get("source", source_keys[0])
        selected_language = request.form.get("language", ALL_LANGUAGES_KEY)
        keyword = (request.form.get("keyword", "") or "").strip()
        parse_article_html = request.form.get("parse_article_html") == "1"
        include_archive = request.form.get("include_archive") == "1"
        try:
            history_pages = int(request.form.get("history_pages", "3"))
        except ValueError:
            history_pages = 3
        history_pages = max(1, min(10, history_pages))
        try:
            limit = int(request.form.get("limit", "12"))
        except ValueError:
            limit = 12
        limit = max(1, min(200, limit))

        results, error = collect_items(
            selected_source,
            selected_language,
            keyword,
            limit,
            history_pages,
            parse_article_html=parse_article_html,
            include_archive=include_archive,
        )

    return render_template(
        "index.html",
        sources=SOURCES,
        language_options=LANGUAGE_OPTIONS,
        selected_source=selected_source,
        selected_language=selected_language,
        limit=limit,
        keyword=keyword,
        history_pages=history_pages,
        parse_article_html=parse_article_html,
        include_archive=include_archive,
        error=error,
        results=results,
    )


@app.route("/export", methods=["POST"])
def export_excel():
    source_keys = [ALL_SOURCES_KEY, *list(SOURCES.keys())]
    selected_source = request.form.get("source", source_keys[0])
    selected_language = request.form.get("language", ALL_LANGUAGES_KEY)
    keyword = (request.form.get("keyword", "") or "").strip()
    parse_article_html = request.form.get("parse_article_html") == "1"
    include_archive = request.form.get("include_archive") == "1"
    try:
        history_pages = int(request.form.get("history_pages", "3"))
    except ValueError:
        history_pages = 3
    history_pages = max(1, min(10, history_pages))

    try:
        limit = int(request.form.get("limit", "12"))
    except ValueError:
        limit = 12
    limit = max(1, min(200, limit))

    results, error = collect_items(
        selected_source,
        selected_language,
        keyword,
        limit,
        history_pages,
        parse_article_html=parse_article_html,
        include_archive=include_archive,
    )
    if error:
        return render_template(
            "index.html",
            sources=SOURCES,
            language_options=LANGUAGE_OPTIONS,
            selected_source=selected_source,
            selected_language=selected_language,
            limit=limit,
            keyword=keyword,
            history_pages=history_pages,
            parse_article_html=parse_article_html,
            include_archive=include_archive,
            error=error,
            results=[],
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "news"
    ws.append(
        [
            "language",
            "source",
            "title",
            "published",
            "summary",
            "body_text",
            "link",
            "keyword",
        ]
    )

    for item in results:
        source_name = item.get("source_name", "")
        language = item.get("language", "")
        if selected_source in SOURCES:
            source = SOURCES[selected_source]
            source_name = source.name
            language = source.language
        ws.append(
            [
                language,
                source_name,
                item.get("title", ""),
                item.get("published", ""),
                item.get("summary", ""),
                item.get("body_text", ""),
                item.get("link", ""),
                keyword,
            ]
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"gov_news_{selected_source}_{stamp}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
