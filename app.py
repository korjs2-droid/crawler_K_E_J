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


ALL_SOURCES_KEY = "all_sources"


SOURCES: dict[str, FeedSource] = {
    "kr_korea_policy": FeedSource(
        key="kr_korea_policy",
        language="한국어",
        name="정책브리핑 (대한민국 정부)",
        feed_url="https://www.korea.kr/rss/policy.xml",
        homepage="https://www.korea.kr/news/policyNewsList.do",
    ),
    "jp_nhk_news": FeedSource(
        key="jp_nhk_news",
        language="日本語",
        name="NHK NEWS (Public Broadcaster)",
        feed_url="https://www3.nhk.or.jp/rss/news/cat0.xml",
        homepage="https://www3.nhk.or.jp/news/",
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
    ),
    "en_nasa_news": FeedSource(
        key="en_nasa_news",
        language="English",
        name="NASA News Releases (U.S. Government)",
        feed_url="https://www.nasa.gov/news-release/feed/",
        homepage="https://www.nasa.gov/news/all-news/",
    ),
}

ARTICLE_SELECTORS: dict[str, tuple[str, ...]] = {
    "kr_korea_policy": ("#newsView p", ".article_txt p", ".view_cont p", "article p"),
    "jp_nhk_news": (".content--detail-body p", ".module--article-body p", "article p"),
    "jp_meti_news": ("#main p", ".main p", ".container p", "article p"),
    "en_nasa_news": (".entry-content p", ".wysiwyg p", "article p"),
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
        return clean_text(text)[:5000]

    soup = BeautifulSoup(html, "html.parser")
    selectors = ARTICLE_SELECTORS.get(source_key, ("article p", "main p", "p"))

    texts: list[str] = []
    for selector in selectors:
        elements = soup.select(selector)
        for elem in elements:
            text = clean_text(elem.get_text(" ", strip=True))
            if len(text) >= 30:
                texts.append(text)
        if len(texts) >= 4:
            break

    if not texts:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        body = soup.find("body")
        if body:
            text = clean_text(body.get_text(" ", strip=True))
            if len(text) >= 40:
                texts.append(text)

    merged = clean_text(" ".join(texts))
    return merged[:5000]


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


def enrich_with_article_bodies(items: list[dict], max_items: int = 40) -> list[dict]:
    if not items:
        return items
    capped = min(len(items), max_items)
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
    keyword: str,
    limit: int,
    history_pages: int = 1,
    parse_article_html: bool = False,
) -> tuple[list[dict], str]:
    # Pull more items before filtering to reduce "too few results" cases.
    fetch_limit = min(200, max(limit, limit * 5 if keyword else limit))

    if selected_source == ALL_SOURCES_KEY:
        merged: list[dict] = []
        errors: list[str] = []
        for source in SOURCES.values():
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

    try:
        crawled = crawl_feed(source, fetch_limit, history_pages=history_pages)
        results = filter_by_keyword(crawled, keyword)[:limit]
        for item in results:
            item["source_name"] = source.name
            item["language"] = source.language
            item["source_key"] = source.key
        if parse_article_html:
            results = enrich_with_article_bodies(results)
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
    limit = 12
    keyword = ""
    history_pages = 3
    parse_article_html = False
    error = ""
    results: list[dict] = []

    if request.method == "POST":
        selected_source = request.form.get("source", source_keys[0])
        keyword = (request.form.get("keyword", "") or "").strip()
        parse_article_html = request.form.get("parse_article_html") == "1"
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
            keyword,
            limit,
            history_pages,
            parse_article_html=parse_article_html,
        )

    return render_template(
        "index.html",
        sources=SOURCES,
        selected_source=selected_source,
        limit=limit,
        keyword=keyword,
        history_pages=history_pages,
        parse_article_html=parse_article_html,
        error=error,
        results=results,
    )


@app.route("/export", methods=["POST"])
def export_excel():
    source_keys = [ALL_SOURCES_KEY, *list(SOURCES.keys())]
    selected_source = request.form.get("source", source_keys[0])
    keyword = (request.form.get("keyword", "") or "").strip()
    parse_article_html = request.form.get("parse_article_html") == "1"
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
        keyword,
        limit,
        history_pages,
        parse_article_html=parse_article_html,
    )
    if error:
        return render_template(
            "index.html",
            sources=SOURCES,
            selected_source=selected_source,
            limit=limit,
            keyword=keyword,
            history_pages=history_pages,
            parse_article_html=parse_article_html,
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
