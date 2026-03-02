from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape

import requests
from flask import Flask, render_template, request

app = Flask(__name__)
TIMEOUT_SECONDS = 12


@dataclass(frozen=True)
class FeedSource:
    key: str
    language: str
    name: str
    feed_url: str
    homepage: str


SOURCES: dict[str, FeedSource] = {
    "kr_korea_policy": FeedSource(
        key="kr_korea_policy",
        language="한국어",
        name="정책브리핑 (대한민국 정부)",
        feed_url="https://www.korea.kr/rss/policy.xml",
        homepage="https://www.korea.kr/news/policyNewsList.do",
    ),
    "jp_meti_news": FeedSource(
        key="jp_meti_news",
        language="日本語",
        name="経済産業省 ニュースリリース",
        feed_url="https://www.meti.go.jp/rss/news_release.xml",
        homepage="https://www.meti.go.jp/english/press/index.html",
    ),
    "en_nasa_news": FeedSource(
        key="en_nasa_news",
        language="English",
        name="NASA News Releases (U.S. Government)",
        feed_url="https://www.nasa.gov/news-release/feed/",
        homepage="https://www.nasa.gov/news/all-news/",
    ),
}


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml_text: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)

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


def crawl_feed(source: FeedSource, limit: int) -> list[dict]:
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    response = requests.get(source.feed_url, timeout=TIMEOUT_SECONDS, headers=headers)
    response.raise_for_status()
    return parse_feed(response.text, limit)


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


@app.route("/", methods=["GET", "POST"])
def index():
    source_keys = list(SOURCES.keys())
    selected_source = source_keys[0]
    limit = 12
    keyword = ""
    error = ""
    results: list[dict] = []

    if request.method == "POST":
        selected_source = request.form.get("source", source_keys[0])
        keyword = (request.form.get("keyword", "") or "").strip()
        try:
            limit = int(request.form.get("limit", "12"))
        except ValueError:
            limit = 12
        limit = max(1, min(50, limit))

        source = SOURCES.get(selected_source)
        if not source:
            error = "지원하지 않는 소스입니다."
        else:
            try:
                crawled = crawl_feed(source, limit)
                results = filter_by_keyword(crawled, keyword)
                if not results:
                    error = "조건에 맞는 결과가 없습니다. 키워드/소스를 바꿔 보세요."
            except requests.RequestException as exc:
                error = f"피드 요청 실패: {exc}"
            except ET.ParseError as exc:
                error = f"피드 파싱 실패: {exc}"

    return render_template(
        "index.html",
        sources=SOURCES,
        selected_source=selected_source,
        limit=limit,
        keyword=keyword,
        error=error,
        results=results,
    )


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
