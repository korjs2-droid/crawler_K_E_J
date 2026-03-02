from __future__ import annotations

from io import BytesIO
import hmac
import os
import re
import threading
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None
from openpyxl import Workbook
import requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

app = Flask(__name__)
TIMEOUT_SECONDS = int(os.getenv("FEED_READ_TIMEOUT", "20"))
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
APP_PASSWORD = os.getenv("WEB_PASSWORD", "news1234")
CRAWL_JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


@dataclass(frozen=True)
class FeedSource:
    key: str
    language: str
    name: str
    feed_url: str
    homepage: str
    source_type: str = "rss"
    fallback_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()


ALL_SOURCES_KEY = "all_sources"
ALL_LANGUAGES_KEY = "all_languages"
ALL_TYPES_KEY = "all_types"

LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    (ALL_LANGUAGES_KEY, "전체"),
    ("한국어", "한국어"),
    ("English", "English"),
    ("日本語", "日本語"),
    ("中文", "中文"),
)
SOURCE_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    (ALL_TYPES_KEY, "전체"),
    ("rss", "RSS/Atom"),
    ("web", "Web 목록"),
)

DISABLED_SOURCES: set[str] = set()


def get_enabled_sources() -> dict[str, FeedSource]:
    return {k: v for k, v in SOURCES.items() if k not in DISABLED_SOURCES}


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
    "jp_yahoo_domestic": FeedSource(
        key="jp_yahoo_domestic",
        language="日本語",
        name="Yahoo!ニュース 国内RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/domestic.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_world": FeedSource(
        key="jp_yahoo_world",
        language="日本語",
        name="Yahoo!ニュース 国際RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/world.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_business": FeedSource(
        key="jp_yahoo_business",
        language="日本語",
        name="Yahoo!ニュース 経済RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/business.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_it": FeedSource(
        key="jp_yahoo_it",
        language="日本語",
        name="Yahoo!ニュース IT・科学RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/it.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_ent": FeedSource(
        key="jp_yahoo_ent",
        language="日本語",
        name="Yahoo!ニュース エンタメRSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/entertainment.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_sports": FeedSource(
        key="jp_yahoo_sports",
        language="日本語",
        name="Yahoo!ニュース スポーツRSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/sports.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_life": FeedSource(
        key="jp_yahoo_life",
        language="日本語",
        name="Yahoo!ニュース ライフRSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/life.xml",
        homepage="https://news.yahoo.co.jp/",
    ),
    "jp_yahoo_local": FeedSource(
        key="jp_yahoo_local",
        language="日本語",
        name="Yahoo!ニュース 地域RSS",
        feed_url="https://news.yahoo.co.jp/rss/topics/local.xml",
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
    "jp_nhk_life": FeedSource(
        key="jp_nhk_life",
        language="日本語",
        name="NHK NEWS 生活・医療RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat2.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_local": FeedSource(
        key="jp_nhk_local",
        language="日本語",
        name="NHK NEWS 地域RSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat3.xml",
        homepage="https://www3.nhk.or.jp/news/",
    ),
    "jp_nhk_sports": FeedSource(
        key="jp_nhk_sports",
        language="日本語",
        name="NHK NEWS スポーツRSS",
        feed_url="https://www3.nhk.or.jp/rss/news/cat8.xml",
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
    "en_bbc_us": FeedSource(
        key="en_bbc_us",
        language="English",
        name="BBC US & Canada RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
        homepage="https://www.bbc.com/news/world/us_and_canada",
    ),
    "en_bbc_europe": FeedSource(
        key="en_bbc_europe",
        language="English",
        name="BBC Europe RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/europe/rss.xml",
        homepage="https://www.bbc.com/news/world/europe",
    ),
    "en_bbc_asia": FeedSource(
        key="en_bbc_asia",
        language="English",
        name="BBC Asia RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        homepage="https://www.bbc.com/news/world/asia",
    ),
    "en_bbc_middle_east": FeedSource(
        key="en_bbc_middle_east",
        language="English",
        name="BBC Middle East RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
        homepage="https://www.bbc.com/news/world/middle_east",
    ),
    "en_bbc_africa": FeedSource(
        key="en_bbc_africa",
        language="English",
        name="BBC Africa RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/africa/rss.xml",
        homepage="https://www.bbc.com/news/world/africa",
    ),
    "en_bbc_latin_america": FeedSource(
        key="en_bbc_latin_america",
        language="English",
        name="BBC Latin America RSS",
        feed_url="https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",
        homepage="https://www.bbc.com/news/world/latin_america",
    ),
    "en_bbc_uk": FeedSource(
        key="en_bbc_uk",
        language="English",
        name="BBC UK RSS",
        feed_url="https://feeds.bbci.co.uk/news/uk/rss.xml",
        homepage="https://www.bbc.com/news/uk",
    ),
    "en_bbc_entertainment": FeedSource(
        key="en_bbc_entertainment",
        language="English",
        name="BBC Entertainment & Arts RSS",
        feed_url="https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        homepage="https://www.bbc.com/news/entertainment_and_arts",
    ),
    "en_nyt_world": FeedSource(
        key="en_nyt_world",
        language="English",
        name="NYTimes World RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        homepage="https://www.nytimes.com/section/world",
    ),
    "en_nyt_business": FeedSource(
        key="en_nyt_business",
        language="English",
        name="NYTimes Business RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        homepage="https://www.nytimes.com/section/business",
    ),
    "en_nyt_tech": FeedSource(
        key="en_nyt_tech",
        language="English",
        name="NYTimes Technology RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
        homepage="https://www.nytimes.com/section/technology",
    ),
    "en_nyt_science": FeedSource(
        key="en_nyt_science",
        language="English",
        name="NYTimes Science RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
        homepage="https://www.nytimes.com/section/science",
    ),
    "en_nyt_health": FeedSource(
        key="en_nyt_health",
        language="English",
        name="NYTimes Health RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
        homepage="https://www.nytimes.com/section/health",
    ),
    "en_nyt_sports": FeedSource(
        key="en_nyt_sports",
        language="English",
        name="NYTimes Sports RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",
        homepage="https://www.nytimes.com/section/sports",
    ),
    "en_nyt_arts": FeedSource(
        key="en_nyt_arts",
        language="English",
        name="NYTimes Arts RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml",
        homepage="https://www.nytimes.com/section/arts",
    ),
    "en_nyt_travel": FeedSource(
        key="en_nyt_travel",
        language="English",
        name="NYTimes Travel RSS",
        feed_url="https://rss.nytimes.com/services/xml/rss/nyt/Travel.xml",
        homepage="https://www.nytimes.com/section/travel",
    ),
    "en_guardian_world": FeedSource(
        key="en_guardian_world",
        language="English",
        name="The Guardian World RSS",
        feed_url="https://www.theguardian.com/world/rss",
        homepage="https://www.theguardian.com/world",
    ),
    "en_guardian_business": FeedSource(
        key="en_guardian_business",
        language="English",
        name="The Guardian Business RSS",
        feed_url="https://www.theguardian.com/uk/business/rss",
        homepage="https://www.theguardian.com/uk/business",
    ),
    "en_guardian_tech": FeedSource(
        key="en_guardian_tech",
        language="English",
        name="The Guardian Technology RSS",
        feed_url="https://www.theguardian.com/uk/technology/rss",
        homepage="https://www.theguardian.com/uk/technology",
    ),
    "en_guardian_science": FeedSource(
        key="en_guardian_science",
        language="English",
        name="The Guardian Science RSS",
        feed_url="https://www.theguardian.com/science/rss",
        homepage="https://www.theguardian.com/science",
    ),
    "en_guardian_environment": FeedSource(
        key="en_guardian_environment",
        language="English",
        name="The Guardian Environment RSS",
        feed_url="https://www.theguardian.com/environment/rss",
        homepage="https://www.theguardian.com/environment",
    ),
    "en_guardian_culture": FeedSource(
        key="en_guardian_culture",
        language="English",
        name="The Guardian Culture RSS",
        feed_url="https://www.theguardian.com/culture/rss",
        homepage="https://www.theguardian.com/culture",
    ),
    "en_guardian_media": FeedSource(
        key="en_guardian_media",
        language="English",
        name="The Guardian Media RSS",
        feed_url="https://www.theguardian.com/media/rss",
        homepage="https://www.theguardian.com/media",
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
    "kr_sbs_society": FeedSource(
        key="kr_sbs_society",
        language="한국어",
        name="SBS 사회 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=05",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=05",
    ),
    "kr_sbs_life": FeedSource(
        key="kr_sbs_life",
        language="한국어",
        name="SBS 생활/문화 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=08",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=08",
    ),
    "kr_sbs_health": FeedSource(
        key="kr_sbs_health",
        language="한국어",
        name="SBS 건강 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=14",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=14",
    ),
    "kr_sbs_sports": FeedSource(
        key="kr_sbs_sports",
        language="한국어",
        name="SBS 스포츠 RSS",
        feed_url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=07",
        homepage="https://news.sbs.co.kr/news/newsSection.do?sectionType=07",
    ),
    "zh_bbc_chinese": FeedSource(
        key="zh_bbc_chinese",
        language="中文",
        name="BBC 中文 RSS",
        feed_url="https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        homepage="https://www.bbc.com/zhongwen/simp",
    ),
    "zh_bbc_traditional": FeedSource(
        key="zh_bbc_traditional",
        language="中文",
        name="BBC 中文繁體 RSS",
        feed_url="https://feeds.bbci.co.uk/zhongwen/trad/rss.xml",
        homepage="https://www.bbc.com/zhongwen/trad",
    ),
    "zh_wikinews": FeedSource(
        key="zh_wikinews",
        language="中文",
        name="维基新闻中文 RSS",
        feed_url="https://zh.wikinews.org/w/index.php?title=Special:%E6%96%B0%E9%97%BB%E8%AE%A2%E9%98%85&feed=rss",
        homepage="https://zh.wikinews.org/",
    ),
    "zh_dw_chinese": FeedSource(
        key="zh_dw_chinese",
        language="中文",
        name="DW 中文 RSS",
        feed_url="https://rss.dw.com/rdf/rss-chi-all",
        homepage="https://www.dw.com/zh/",
    ),
    "kr_naver_d2": FeedSource(
        key="kr_naver_d2",
        language="한국어",
        name="NAVER D2 기술블로그 Atom",
        feed_url="https://d2.naver.com/d2.atom",
        homepage="https://d2.naver.com/home",
    ),
    "kr_kakao_tech": FeedSource(
        key="kr_kakao_tech",
        language="한국어",
        name="Kakao Tech Blog RSS",
        feed_url="https://tech.kakao.com/feed/",
        homepage="https://tech.kakao.com/blog/",
    ),
    "kr_ruliweb_dev": FeedSource(
        key="kr_ruliweb_dev",
        language="한국어",
        name="Ruliweb 개발정보 RSS",
        feed_url="https://bbs.ruliweb.com/news/board/1005/rss",
        homepage="https://bbs.ruliweb.com/news/board/1005",
    ),
    "jp_qiita_popular": FeedSource(
        key="jp_qiita_popular",
        language="日本語",
        name="Qiita 人気記事 Atom",
        feed_url="https://qiita.com/popular-items/feed.atom",
        homepage="https://qiita.com/",
    ),
    "jp_classmethod_dev": FeedSource(
        key="jp_classmethod_dev",
        language="日本語",
        name="DevelopersIO RSS",
        feed_url="https://dev.classmethod.jp/feed/",
        homepage="https://dev.classmethod.jp/",
    ),
    "jp_publickey": FeedSource(
        key="jp_publickey",
        language="日本語",
        name="Publickey RSS",
        feed_url="https://www.publickey1.jp/atom.xml",
        homepage="https://www.publickey1.jp/",
    ),
    "en_arxiv_cs_ai": FeedSource(
        key="en_arxiv_cs_ai",
        language="English",
        name="arXiv cs.AI RSS",
        feed_url="https://export.arxiv.org/rss/cs.AI",
        homepage="https://arxiv.org/list/cs.AI/recent",
    ),
    "en_arxiv_cs_lg": FeedSource(
        key="en_arxiv_cs_lg",
        language="English",
        name="arXiv cs.LG RSS",
        feed_url="https://export.arxiv.org/rss/cs.LG",
        homepage="https://arxiv.org/list/cs.LG/recent",
    ),
    "en_arxiv_cs_cl": FeedSource(
        key="en_arxiv_cs_cl",
        language="English",
        name="arXiv cs.CL RSS",
        feed_url="https://export.arxiv.org/rss/cs.CL",
        homepage="https://arxiv.org/list/cs.CL/recent",
    ),
    "en_arxiv_stat_ml": FeedSource(
        key="en_arxiv_stat_ml",
        language="English",
        name="arXiv stat.ML RSS",
        feed_url="https://export.arxiv.org/rss/stat.ML",
        homepage="https://arxiv.org/list/stat.ML/recent",
    ),
    "en_hn_newest": FeedSource(
        key="en_hn_newest",
        language="English",
        name="Hacker News Newest RSS",
        feed_url="https://hnrss.org/newest",
        homepage="https://news.ycombinator.com/newest",
    ),
    "en_stackoverflow_blog": FeedSource(
        key="en_stackoverflow_blog",
        language="English",
        name="Stack Overflow Blog RSS",
        feed_url="https://stackoverflow.blog/feed/",
        homepage="https://stackoverflow.blog/",
    ),
    "en_github_blog": FeedSource(
        key="en_github_blog",
        language="English",
        name="GitHub Blog RSS",
        feed_url="https://github.blog/feed/",
        homepage="https://github.blog/",
    ),
    "zh_ruanyifeng": FeedSource(
        key="zh_ruanyifeng",
        language="中文",
        name="阮一峰博客 Atom",
        feed_url="https://www.ruanyifeng.com/blog/atom.xml",
        homepage="https://www.ruanyifeng.com/blog/",
    ),
    "zh_infoq": FeedSource(
        key="zh_infoq",
        language="中文",
        name="InfoQ 中文 RSS",
        feed_url="https://www.infoq.cn/feed",
        homepage="https://www.infoq.cn/",
    ),
    "zh_ithome": FeedSource(
        key="zh_ithome",
        language="中文",
        name="IT之家 RSS",
        feed_url="https://www.ithome.com/rss/",
        homepage="https://www.ithome.com/",
    ),
    "zh_cna_top": FeedSource(
        key="zh_cna_top",
        language="中文",
        name="中央社 即時新聞 RSS",
        feed_url="https://www.cna.com.tw/rss/aall.xml",
        homepage="https://www.cna.com.tw/",
    ),
    "kr_seoul_news_web": FeedSource(
        key="kr_seoul_news_web",
        language="한국어",
        name="서울시 보도자료 (Web)",
        feed_url="https://www.seoul.go.kr/news/news_report.do",
        homepage="https://www.seoul.go.kr/",
        source_type="web",
    ),
    "kr_gov24_notice_web": FeedSource(
        key="kr_gov24_notice_web",
        language="한국어",
        name="정부24 공지사항 (Web)",
        feed_url="https://www.gov.kr/portal/ntcItm",
        homepage="https://www.gov.kr/",
        source_type="web",
    ),
    "jp_prime_minister_web": FeedSource(
        key="jp_prime_minister_web",
        language="日本語",
        name="首相官邸 トピックス (Web)",
        feed_url="https://www.kantei.go.jp/jp/headline/index.html",
        homepage="https://www.kantei.go.jp/",
        source_type="web",
    ),
    "jp_cao_news_web": FeedSource(
        key="jp_cao_news_web",
        language="日本語",
        name="内閣府 新着情報 (Web)",
        feed_url="https://www.cao.go.jp/",
        homepage="https://www.cao.go.jp/",
        source_type="web",
    ),
    "en_govuk_news_web": FeedSource(
        key="en_govuk_news_web",
        language="English",
        name="UK GOV News (Web)",
        feed_url="https://www.gov.uk/search/news-and-communications",
        homepage="https://www.gov.uk/",
        source_type="web",
    ),
    "en_who_news_web": FeedSource(
        key="en_who_news_web",
        language="English",
        name="WHO Newsroom (Web)",
        feed_url="https://www.who.int/news-room/releases",
        homepage="https://www.who.int/",
        source_type="web",
    ),
    "zh_gov_cn_web": FeedSource(
        key="zh_gov_cn_web",
        language="中文",
        name="中国政府网 要闻 (Web)",
        feed_url="https://www.gov.cn/yaowen/",
        homepage="https://www.gov.cn/",
        source_type="web",
    ),
    "zh_xinhuanet_web": FeedSource(
        key="zh_xinhuanet_web",
        language="中文",
        name="新华网 要闻 (Web)",
        feed_url="http://www.news.cn/",
        homepage="http://www.news.cn/",
        source_type="web",
    ),
    "kr_hani_news": FeedSource(
        key="kr_hani_news",
        language="한국어",
        name="한겨레 뉴스 RSS",
        feed_url="https://www.hani.co.kr/rss/",
        homepage="https://www.hani.co.kr/",
    ),
    "kr_donga_total": FeedSource(
        key="kr_donga_total",
        language="한국어",
        name="동아일보 전체 RSS",
        feed_url="https://rss.donga.com/total.xml",
        homepage="https://www.donga.com/",
    ),
    "kr_mk_economy": FeedSource(
        key="kr_mk_economy",
        language="한국어",
        name="매일경제 경제 RSS",
        feed_url="https://www.mk.co.kr/rss/30100041/",
        homepage="https://www.mk.co.kr/",
    ),
    "kr_mk_society": FeedSource(
        key="kr_mk_society",
        language="한국어",
        name="매일경제 사회 RSS",
        feed_url="https://www.mk.co.kr/rss/50400012/",
        homepage="https://www.mk.co.kr/",
    ),
    "kr_mk_world": FeedSource(
        key="kr_mk_world",
        language="한국어",
        name="매일경제 국제 RSS",
        feed_url="https://www.mk.co.kr/rss/30300018/",
        homepage="https://www.mk.co.kr/",
    ),
    "kr_hankyung_all": FeedSource(
        key="kr_hankyung_all",
        language="한국어",
        name="한국경제 전체 RSS",
        feed_url="https://www.hankyung.com/feed/all-news",
        homepage="https://www.hankyung.com/",
    ),
    "jp_asahi_headlines": FeedSource(
        key="jp_asahi_headlines",
        language="日本語",
        name="朝日新聞 ヘッドラインRSS",
        feed_url="https://www.asahi.com/rss/asahi/newsheadlines.rdf",
        homepage="https://www.asahi.com/",
    ),
    "jp_livedoor_top": FeedSource(
        key="jp_livedoor_top",
        language="日本語",
        name="livedoorニュース 主要RSS",
        feed_url="https://news.livedoor.com/topics/rss/top.xml",
        homepage="https://news.livedoor.com/",
    ),
    "jp_livedoor_dom": FeedSource(
        key="jp_livedoor_dom",
        language="日本語",
        name="livedoorニュース 国内RSS",
        feed_url="https://news.livedoor.com/topics/rss/dom.xml",
        homepage="https://news.livedoor.com/",
    ),
    "jp_livedoor_int": FeedSource(
        key="jp_livedoor_int",
        language="日本語",
        name="livedoorニュース 海外RSS",
        feed_url="https://news.livedoor.com/topics/rss/int.xml",
        homepage="https://news.livedoor.com/",
    ),
    "jp_livedoor_eco": FeedSource(
        key="jp_livedoor_eco",
        language="日本語",
        name="livedoorニュース 経済RSS",
        feed_url="https://news.livedoor.com/topics/rss/eco.xml",
        homepage="https://news.livedoor.com/",
    ),
    "jp_livedoor_ent": FeedSource(
        key="jp_livedoor_ent",
        language="日本語",
        name="livedoorニュース エンタメRSS",
        feed_url="https://news.livedoor.com/topics/rss/ent.xml",
        homepage="https://news.livedoor.com/",
    ),
    "jp_livedoor_sports": FeedSource(
        key="jp_livedoor_sports",
        language="日本語",
        name="livedoorニュース スポーツRSS",
        feed_url="https://news.livedoor.com/topics/rss/spo.xml",
        homepage="https://news.livedoor.com/",
    ),
    "en_cnn_top": FeedSource(
        key="en_cnn_top",
        language="English",
        name="CNN Top Stories RSS",
        feed_url="http://rss.cnn.com/rss/edition.rss",
        homepage="https://edition.cnn.com/",
    ),
    "en_cnn_world": FeedSource(
        key="en_cnn_world",
        language="English",
        name="CNN World RSS",
        feed_url="http://rss.cnn.com/rss/edition_world.rss",
        homepage="https://edition.cnn.com/world",
    ),
    "en_cnn_us": FeedSource(
        key="en_cnn_us",
        language="English",
        name="CNN U.S. RSS",
        feed_url="http://rss.cnn.com/rss/edition_us.rss",
        homepage="https://edition.cnn.com/us",
    ),
    "en_cnn_business": FeedSource(
        key="en_cnn_business",
        language="English",
        name="CNN Business RSS",
        feed_url="http://rss.cnn.com/rss/money_latest.rss",
        homepage="https://www.cnn.com/business",
    ),
    "en_cnn_tech": FeedSource(
        key="en_cnn_tech",
        language="English",
        name="CNN Technology RSS",
        feed_url="http://rss.cnn.com/rss/edition_technology.rss",
        homepage="https://edition.cnn.com/business/tech",
    ),
    "en_aljazeera_all": FeedSource(
        key="en_aljazeera_all",
        language="English",
        name="Al Jazeera All News RSS",
        feed_url="https://www.aljazeera.com/xml/rss/all.xml",
        homepage="https://www.aljazeera.com/",
    ),
    "en_theverge": FeedSource(
        key="en_theverge",
        language="English",
        name="The Verge RSS",
        feed_url="https://www.theverge.com/rss/index.xml",
        homepage="https://www.theverge.com/",
    ),
    "zh_ftchinese": FeedSource(
        key="zh_ftchinese",
        language="中文",
        name="FT中文网 RSS",
        feed_url="https://www.ftchinese.com/rss/feed",
        homepage="https://www.ftchinese.com/",
    ),
    "zh_zaobao": FeedSource(
        key="zh_zaobao",
        language="中文",
        name="联合早报 RSS",
        feed_url="https://www.zaobao.com.sg/rss.xml",
        homepage="https://www.zaobao.com.sg/",
    ),
    "zh_udn_global": FeedSource(
        key="zh_udn_global",
        language="中文",
        name="聯合新聞網 全球 RSS",
        feed_url="https://udn.com/rssfeed/news/2/6638?ch=news",
        homepage="https://udn.com/",
    ),
    "zh_udn_mainland": FeedSource(
        key="zh_udn_mainland",
        language="中文",
        name="聯合新聞網 兩岸 RSS",
        feed_url="https://udn.com/rssfeed/news/2/6640?ch=news",
        homepage="https://udn.com/",
    ),
    "zh_udn_instant": FeedSource(
        key="zh_udn_instant",
        language="中文",
        name="聯合新聞網 即時新聞 RSS",
        feed_url="https://udn.com/rssfeed/news/1/0?ch=news",
        homepage="https://udn.com/",
    ),
    "kr_kdca_press": FeedSource(
        key="kr_kdca_press",
        language="한국어",
        name="질병관리청 보도자료 RSS",
        feed_url="https://www.kdca.go.kr/bbs/kdca/41/rssList.do?row=50",
        homepage="https://www.kdca.go.kr/",
    ),
    "kr_kdca_notice": FeedSource(
        key="kr_kdca_notice",
        language="한국어",
        name="질병관리청 공지사항 RSS",
        feed_url="https://www.kdca.go.kr/bbs/kdca/51/rssList.do?row=50",
        homepage="https://www.kdca.go.kr/",
    ),
    "kr_donga_politics": FeedSource(
        key="kr_donga_politics",
        language="한국어",
        name="동아일보 정치 RSS",
        feed_url="https://rss.donga.com/politics.xml",
        homepage="https://www.donga.com/",
    ),
    "kr_donga_science": FeedSource(
        key="kr_donga_science",
        language="한국어",
        name="동아일보 의학과학 RSS",
        feed_url="https://rss.donga.com/science.xml",
        homepage="https://www.donga.com/",
    ),
    "kr_donga_culture": FeedSource(
        key="kr_donga_culture",
        language="한국어",
        name="동아일보 문화연예 RSS",
        feed_url="https://rss.donga.com/culture.xml",
        homepage="https://www.donga.com/",
    ),
    "kr_donga_health": FeedSource(
        key="kr_donga_health",
        language="한국어",
        name="동아일보 건강 RSS",
        feed_url="https://rss.donga.com/health.xml",
        homepage="https://www.donga.com/",
    ),
    "jp_asahi_international": FeedSource(
        key="jp_asahi_international",
        language="日本語",
        name="朝日新聞 国際 RSS",
        feed_url="https://www.asahi.com/rss/asahi/international.rdf",
        homepage="https://www.asahi.com/",
    ),
    "jp_asahi_business": FeedSource(
        key="jp_asahi_business",
        language="日本語",
        name="朝日新聞 経済 RSS",
        feed_url="https://www.asahi.com/rss/asahi/business.rdf",
        homepage="https://www.asahi.com/",
    ),
    "jp_asahi_politics": FeedSource(
        key="jp_asahi_politics",
        language="日本語",
        name="朝日新聞 政治 RSS",
        feed_url="https://www.asahi.com/rss/asahi/politics.rdf",
        homepage="https://www.asahi.com/",
    ),
    "jp_asahi_society": FeedSource(
        key="jp_asahi_society",
        language="日本語",
        name="朝日新聞 社会 RSS",
        feed_url="https://www.asahi.com/rss/asahi/national.rdf",
        homepage="https://www.asahi.com/",
    ),
    "en_dw_world": FeedSource(
        key="en_dw_world",
        language="English",
        name="DW World RSS",
        feed_url="https://rss.dw.com/rdf/rss-en-world",
        homepage="https://www.dw.com/en/",
    ),
    "en_dw_business": FeedSource(
        key="en_dw_business",
        language="English",
        name="DW Business RSS",
        feed_url="https://rss.dw.com/rdf/rss-en-bus",
        homepage="https://www.dw.com/en/",
    ),
    "en_dw_science": FeedSource(
        key="en_dw_science",
        language="English",
        name="DW Science RSS",
        feed_url="https://rss.dw.com/xml/rss_en_science",
        homepage="https://www.dw.com/en/",
    ),
    "en_abc_top": FeedSource(
        key="en_abc_top",
        language="English",
        name="ABC News Top Stories RSS",
        feed_url="https://abcnews.go.com/abcnews/topstories",
        homepage="https://abcnews.go.com/",
        source_type="web",
    ),
    "zh_govhk_top": FeedSource(
        key="zh_govhk_top",
        language="中文",
        name="GovHK Top Stories RSS",
        feed_url="https://www.news.gov.hk/en/common/html/topstories.rss.xml",
        homepage="https://www.news.gov.hk/",
    ),
    "zh_govhk_finance": FeedSource(
        key="zh_govhk_finance",
        language="中文",
        name="GovHK Business & Finance RSS",
        feed_url="https://www.news.gov.hk/en/categories/finance/html/articlelist.rss.xml",
        homepage="https://www.news.gov.hk/",
    ),
    "zh_govhk_health": FeedSource(
        key="zh_govhk_health",
        language="中文",
        name="GovHK Health & Community RSS",
        feed_url="https://www.news.gov.hk/en/categories/health/html/articlelist.rss.xml",
        homepage="https://www.news.gov.hk/",
    ),
    "zh_cna_politics": FeedSource(
        key="zh_cna_politics",
        language="中文",
        name="中央社 政治 RSS",
        feed_url="https://feeds.feedburner.com/rsscna/politics",
        homepage="https://www.cna.com.tw/",
    ),
    "zh_cna_world": FeedSource(
        key="zh_cna_world",
        language="中文",
        name="中央社 國際 RSS",
        feed_url="https://feeds.feedburner.com/rsscna/intworld",
        homepage="https://www.cna.com.tw/",
    ),
    "zh_cna_mainland": FeedSource(
        key="zh_cna_mainland",
        language="中文",
        name="中央社 兩岸 RSS",
        feed_url="https://feeds.feedburner.com/rsscna/mainland",
        homepage="https://www.cna.com.tw/",
    ),
    "zh_cna_finance": FeedSource(
        key="zh_cna_finance",
        language="中文",
        name="中央社 財經 RSS",
        feed_url="https://feeds.feedburner.com/rsscna/finance",
        homepage="https://www.cna.com.tw/",
    ),
    "zh_cna_tech": FeedSource(
        key="zh_cna_tech",
        language="中文",
        name="中央社 科技 RSS",
        feed_url="https://feeds.feedburner.com/rsscna/technology",
        homepage="https://www.cna.com.tw/",
    ),
}

ARTICLE_SELECTORS: dict[str, tuple[str, ...]] = {
    "kr_korea_policy": ("#newsView p", ".article_txt p", ".view_cont p", "article p"),
    "kr_yna_news": ("#articleWrap p", ".story-news p", "article p"),
    "kr_sbs_news": (".main_text p", "#container p", "article p"),
    "kr_sbs_politics": (".main_text p", "#container p", "article p"),
    "kr_sbs_economy": (".main_text p", "#container p", "article p"),
    "kr_sbs_world": (".main_text p", "#container p", "article p"),
    "jp_nhk_news": (".content--detail-body", ".module--article-body", "article", "main"),
    "jp_mainichi_news": (".articledetail-body", ".article-body", "article", "main"),
    "jp_yahoo_news": ("article", "main article", "main", "[class*='article']"),
    "jp_nhk_society": (".content--detail-body", ".module--article-body", "article", "main"),
    "jp_nhk_world": (".content--detail-body", ".module--article-body", "article", "main"),
    "jp_nhk_politics": (".content--detail-body", ".module--article-body", "article", "main"),
    "jp_nhk_economy": (".content--detail-body", ".module--article-body", "article", "main"),
    "jp_nhk_science": (".content--detail-body", ".module--article-body", "article", "main"),
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
    try:
        root = ET.fromstring(xml_data)
    except ValueError as exc:
        # Expat in stdlib may reject some multi-byte legacy encodings (e.g., GB2312).
        if not isinstance(xml_data, bytes):
            raise
        head = xml_data[:200].decode("ascii", errors="ignore")
        match = re.search(r'encoding=["\\\']([A-Za-z0-9_-]+)["\\\']', head)
        declared = match.group(1) if match else ""
        candidates = [declared, "utf-8", "gb18030", "big5"]
        last_error: Exception | None = exc
        for enc in candidates:
            if not enc:
                continue
            try:
                decoded = xml_data.decode(enc, errors="strict")
                root = ET.fromstring(decoded)
                break
            except Exception as dec_exc:  # pragma: no cover
                last_error = dec_exc
        else:
            raise last_error if last_error else exc

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


def resolve_list_selectors(source_key: str) -> tuple[str, ...]:
    if source_key.startswith("kr_seoul_news_web"):
        return ("#list_view li a", ".news_list a", "a")
    if source_key.startswith("kr_gov24_notice_web"):
        return (".board-list a", ".table a", "a")
    if source_key.startswith("jp_prime_minister_web"):
        return (".section a", ".content a", "a")
    if source_key.startswith("jp_cao_news_web"):
        return ("#main a", ".main a", "a")
    if source_key.startswith("en_govuk_news_web"):
        return (".gem-c-document-list a", ".govuk-link", "a")
    if source_key.startswith("en_who_news_web"):
        return (".list-view--item a", ".sf-content-block a", "a")
    if source_key.startswith("zh_gov_cn_web"):
        return (".list a", ".news_box a", "a")
    if source_key.startswith("zh_xinhuanet_web"):
        return (".yaowen a", ".newsList a", "a")
    return ("article a", "main a", "a")


def extract_links_from_list(source: FeedSource, html: bytes | str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser") if BeautifulSoup is not None else None
    if soup is None:
        return []
    selectors = resolve_list_selectors(source.key)
    results: list[dict] = []
    seen: set[str] = set()
    home_host = urlparse(source.homepage).netloc
    for selector in selectors:
        for elem in soup.select(selector):
            href = elem.get("href") if elem.has_attr("href") else ""
            if not href:
                link_tag = elem.find("a")
                href = link_tag.get("href") if link_tag else ""
            if not href:
                continue
            url = urljoin(page_url, href.strip())
            host = urlparse(url).netloc
            if home_host and host and home_host not in host and host not in home_host:
                continue
            if url in seen:
                continue
            seen.add(url)
            title = clean_text(elem.get_text(" ", strip=True))
            if len(title) < 2:
                continue
            results.append({"link": url, "title": title})
    return results


def crawl_web_source(source: FeedSource, limit: int, history_pages: int = 1) -> list[dict]:
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    max_pages = max(1, min(10, history_pages))
    candidates: list[dict] = []
    seen_links: set[str] = set()
    for page in range(1, max_pages + 1):
        page_url = build_paged_url(source.feed_url, page)
        try:
            resp = requests.get(page_url, timeout=(8, TIMEOUT_SECONDS), headers=headers)
            resp.raise_for_status()
            links = extract_links_from_list(source, resp.content, page_url)
            for item in links:
                link = item.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                candidates.append(item)
                if len(candidates) >= limit * 4:
                    break
        except requests.RequestException:
            if page == 1:
                break
        if len(candidates) >= limit * 4:
            break

    items: list[dict] = []
    for cand in candidates:
        link = cand.get("link", "")
        title = cand.get("title", "")
        body = fetch_article_body(source.key, link)
        if not body:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "published": "",
                "summary": body[:320],
                "body_text": body,
            }
        )
        if len(items) >= limit:
            break
    return items


def parse_sitemap_bundle(xml_data: bytes | str) -> tuple[list[str], list[str]]:
    root = ET.fromstring(xml_data)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    sitemap_urls: list[str] = []
    for loc in root.findall(".//sm:url/sm:loc", ns):
        if loc.text:
            urls.append(clean_text(loc.text))
    for loc in root.findall(".//sm:sitemap/sm:loc", ns):
        if loc.text:
            sitemap_urls.append(clean_text(loc.text))
    return ([u for u in urls if u], [u for u in sitemap_urls if u])


def collect_archive_items(source: FeedSource, limit: int) -> list[dict]:
    if source.source_type == "web":
        return []

    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    candidates: list[str] = []
    visited_sitemaps: set[str] = set()
    to_visit: list[str] = list(source.sitemap_urls)
    if not to_visit:
        to_visit.append(urljoin(source.homepage, "/sitemap.xml"))

    max_sitemaps = 20
    while to_visit and len(visited_sitemaps) < max_sitemaps:
        sitemap_url = to_visit.pop(0)
        if sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)

        try:
            res = requests.get(sitemap_url, timeout=(8, TIMEOUT_SECONDS), headers=headers)
            res.raise_for_status()
            urls, nested_sitemaps = parse_sitemap_bundle(res.content)
            for nested in nested_sitemaps[:8]:
                if nested not in visited_sitemaps:
                    to_visit.append(nested)
            # Keep only article-like links for each source.
            if source.key == "kr_korea_policy":
                urls = [u for u in urls if "/news/" in u and ".do?" in u]
            elif source.key == "jp_nhk_news":
                urls = [u for u in urls if "/news/html/" in u]
            elif source.key.startswith("jp_nhk_"):
                urls = [u for u in urls if "/news/html/" in u]
            elif source.key == "en_nasa_news":
                urls = [u for u in urls if "/news-release/" in u or "/news/" in u]
            elif source.key.startswith("en_bbc_"):
                urls = [u for u in urls if "/news/" in u]
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
    if source.source_type == "web":
        return crawl_web_source(source, limit, history_pages)

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


def resolve_selectors(source_key: str) -> tuple[str, ...]:
    if source_key.startswith("kr_sbs_"):
        return (".main_text", "#container", "article", "main")
    if source_key.startswith("jp_nhk_"):
        return (".content--detail-body", ".module--article-body", "article", "main")
    if source_key.startswith("jp_yahoo_"):
        return ("article", "main article", "main", "[class*='article']")
    if source_key.startswith("en_bbc_"):
        return ("[data-component='text-block']", "article", "main")
    if source_key.startswith("en_nyt_"):
        return ("section[name='articleBody']", ".StoryBodyCompanionColumn", "article", "main")
    if source_key.startswith("en_guardian_"):
        return ("#maincontent", "article", "main")
    if source_key.startswith("zh_bbc_"):
        return ("article", "main", "[data-component='text-block']")
    if source_key.startswith("zh_wikinews"):
        return ("#mw-content-text", "article", "main")
    if source_key.startswith("zh_dw_"):
        return ("article", "main", ".rich-text")
    if source_key.startswith("kr_naver_d2") or source_key.startswith("kr_kakao_tech"):
        return ("article", ".entry-content", ".post-content", "main")
    if source_key.startswith("kr_ruliweb_"):
        return ("#board_read", ".view_content", "article", "main")
    if source_key.startswith("jp_qiita_"):
        return ("article", ".it-MdContent", "main")
    if source_key.startswith("jp_classmethod_") or source_key.startswith("jp_publickey"):
        return ("article", ".entry-content", "#main", "main")
    if source_key.startswith("en_arxiv_"):
        return ("#content", "main", "article")
    if source_key.startswith("en_hn_"):
        return ("main", "body")
    if source_key.startswith("en_stackoverflow_") or source_key.startswith("en_github_blog"):
        return ("article", ".post-content", "main")
    if source_key.startswith("zh_ruanyifeng") or source_key.startswith("zh_infoq") or source_key.startswith("zh_ithome"):
        return ("article", ".article-content", ".post-content", "main")
    return ARTICLE_SELECTORS.get(source_key, ("article", "main", "p"))


def extract_article_text(source_key: str, html: bytes | str) -> str:
    if BeautifulSoup is None:
        if isinstance(html, bytes):
            text = html.decode("utf-8", errors="ignore")
        else:
            text = html
        return clean_text(text)[:50000]

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    selectors = resolve_selectors(source_key)
    jp_source = source_key.startswith("jp_")

    def text_score(text: str) -> float:
        if not text:
            return 0.0
        score = float(len(text))
        if jp_source:
            jp_chars = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
            score *= 1.0 + (jp_chars / max(len(text), 1))
        return score

    best_text = ""
    for selector in selectors:
        elements = soup.select(selector)
        if not elements:
            continue

        parts: list[str] = []
        seen: set[str] = set()
        for elem in elements:
            block_nodes = elem.select("p, li, h2, h3, h4, div")
            if block_nodes:
                for node in block_nodes:
                    if node.name == "div" and node.select("p, li, h2, h3, h4"):
                        continue
                    text = clean_text(node.get_text(" ", strip=True))
                    if len(text) < 8:
                        continue
                    if text in seen:
                        continue
                    seen.add(text)
                    parts.append(text)
            else:
                own_text = clean_text(elem.get_text(" ", strip=True))
                if len(own_text) >= 8 and own_text not in seen:
                    seen.add(own_text)
                    parts.append(own_text)

        candidate = clean_text(" ".join(parts))
        if text_score(candidate) > text_score(best_text):
            best_text = candidate

    if not best_text:
        fallback_candidates: list[str] = []
        for elem in soup.select("article, main, [role='main'], #main, .main, .content, .article"):
            text = clean_text(elem.get_text(" ", strip=True))
            if len(text) >= 80:
                fallback_candidates.append(text)
        body = soup.find("body")
        if body:
            fallback_candidates.append(clean_text(body.get_text(" ", strip=True)))
        if fallback_candidates:
            best_text = max(fallback_candidates, key=text_score)

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
    selected_type: str,
    keyword: str,
    limit: int,
    history_pages: int = 1,
    parse_article_html: bool = True,
    include_archive: bool = True,
    fill_with_general: bool = True,
    min_per_source: int = 1,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[list[dict], str]:
    # Keep full article-body extraction always enabled for KR/EN/JP output quality.
    parse_article_html = True
    # Pull more items before filtering to reduce "too few results" cases.
    fetch_limit = min(600, max(limit * 3, limit * 20 if keyword else limit * 6))
    enabled_sources = get_enabled_sources()
    target_sources = [
        s
        for s in enabled_sources.values()
        if (selected_language == ALL_LANGUAGES_KEY or s.language == selected_language)
        and (selected_type == ALL_TYPES_KEY or s.source_type == selected_type)
    ]

    if selected_source == ALL_SOURCES_KEY:
        if not target_sources:
            return [], "선택한 언어에 해당하는 소스가 없습니다."
        merged: list[dict] = []
        per_source: dict[str, list[dict]] = {}
        errors: list[str] = []
        total_sources = len(target_sources)
        for idx, source in enumerate(target_sources, start=1):
            if progress_callback:
                pct = int(5 + (idx - 1) / total_sources * 70)
                progress_callback(pct, f"{source.name} 수집 중")
            try:
                crawled = crawl_feed(source, fetch_limit, history_pages=history_pages)
                for item in crawled:
                    item["source_name"] = source.name
                    item["language"] = source.language
                    item["source_key"] = source.key
                merged.extend(crawled)
                per_source[source.key] = list(crawled)
            except requests.RequestException:
                errors.append(source.name)
            except ET.ParseError:
                errors.append(source.name)
            if include_archive:
                archive_items = collect_archive_items(source, min(fetch_limit, 80))
                for item in archive_items:
                    item["source_name"] = source.name
                    item["language"] = source.language
                    item["source_key"] = source.key
                merged.extend(archive_items)
                per_source.setdefault(source.key, []).extend(archive_items)
        if progress_callback:
            progress_callback(80, "키워드 필터 적용 중")

        target_min = max(0, min(min_per_source, 10))
        results: list[dict] = []
        chosen = set()

        if target_min > 0 and keyword:
            for source in target_sources:
                source_items = per_source.get(source.key, [])
                source_filtered = filter_by_keyword(source_items, keyword)
                picked = 0
                for item in source_filtered:
                    key = item.get("link", "") or item.get("title", "")
                    if key in chosen:
                        continue
                    chosen.add(key)
                    results.append(item)
                    picked += 1
                    if picked >= target_min or len(results) >= limit:
                        break
                if len(results) >= limit:
                    break

        filtered = filter_by_keyword(merged, keyword)
        for item in filtered:
            key = item.get("link", "") or item.get("title", "")
            if key in chosen:
                continue
            chosen.add(key)
            results.append(item)
            if len(results) >= limit:
                break

        used_fallback_fill = False
        if fill_with_general and len(results) < limit:
            for item in merged:
                key = item.get("link", "") or item.get("title", "")
                if key in chosen:
                    continue
                chosen.add(key)
                results.append(item)
                used_fallback_fill = True
                if len(results) >= limit:
                    break
        if results:
            if parse_article_html:
                if progress_callback:
                    progress_callback(85, "기사 본문 파싱 중")
                results = enrich_with_article_bodies(results)
            if progress_callback:
                progress_callback(100, "완료")
            if used_fallback_fill and keyword:
                return results, "키워드 일치 결과가 부족해 일부 일반 기사로 채웠습니다."
            if errors:
                return results, f"일부 소스 실패: {', '.join(errors[:2])}"
            return results, ""
        if errors:
            return [], f"전체 소스 요청 실패: {', '.join(errors[:3])}"
        return [], "조건에 맞는 결과가 없습니다. 키워드/소스를 바꿔 보세요."

    source = enabled_sources.get(selected_source)
    if not source:
        return [], "지원하지 않는 소스입니다."
    if selected_language != ALL_LANGUAGES_KEY and source.language != selected_language:
        return [], "선택한 언어와 뉴스 소스가 일치하지 않습니다."

    try:
        if progress_callback:
            progress_callback(20, f"{source.name} 피드 수집 중")
        crawled = crawl_feed(source, fetch_limit, history_pages=history_pages)
        if progress_callback:
            progress_callback(60, "키워드 필터 적용 중")
        filtered = filter_by_keyword(crawled, keyword)
        results = filtered[:limit]
        used_fallback_fill = False
        for item in results:
            item["source_name"] = source.name
            item["language"] = source.language
            item["source_key"] = source.key
        if parse_article_html:
            if progress_callback:
                progress_callback(80, "기사 본문 파싱 중")
            results = enrich_with_article_bodies(results)
        if include_archive and len(results) < limit:
            if progress_callback:
                progress_callback(88, "아카이브 수집 중")
            archive_items = collect_archive_items(source, min(fetch_limit, 80))
            archive_items = filter_by_keyword(archive_items, keyword)
            results.extend(archive_items)
            dedup: dict[str, dict] = {}
            for item in results:
                dedup[item.get("link", "") or item.get("title", "")] = item
            results = list(dedup.values())[:limit]
        if fill_with_general and len(results) < limit:
            seen = {item.get("link", "") or item.get("title", "") for item in results}
            for item in crawled:
                key = item.get("link", "") or item.get("title", "")
                if key in seen:
                    continue
                item["source_name"] = source.name
                item["language"] = source.language
                item["source_key"] = source.key
                seen.add(key)
                results.append(item)
                used_fallback_fill = True
                if len(results) >= limit:
                    break
        if progress_callback:
            progress_callback(100, "완료")
        if not results:
            return [], "조건에 맞는 결과가 없습니다. 키워드/소스를 바꿔 보세요."
        if used_fallback_fill and keyword:
            return results, "키워드 일치 결과가 부족해 일부 일반 기사로 채웠습니다."
        return results, ""
    except requests.RequestException as exc:
        return [], f"피드 요청 실패: {exc}"
    except ET.ParseError as exc:
        return [], f"피드 파싱 실패: {exc}"


@app.before_request
def require_login():
    allowed = {"login", "static"}
    if request.endpoint in allowed:
        return None
    if session.get("authenticated"):
        return None
    if request.path.startswith("/crawl/"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, APP_PASSWORD):
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def _set_job(job_id: str, payload: dict) -> None:
    with JOBS_LOCK:
        CRAWL_JOBS[job_id] = {**CRAWL_JOBS.get(job_id, {}), **payload}


def build_query_signature(
    selected_source: str,
    selected_language: str,
    selected_type: str,
    keyword: str,
    limit: int,
    history_pages: int,
    include_archive: bool,
    fill_with_general: bool,
    min_per_source: int,
) -> str:
    return "|".join(
        [
            selected_source,
            selected_language,
            selected_type,
            keyword.strip().lower(),
            str(limit),
            str(history_pages),
            "1" if include_archive else "0",
            "1" if fill_with_general else "0",
            str(min_per_source),
        ]
    )


def _run_job(job_id: str, params: dict) -> None:
    def progress(pct: int, message: str) -> None:
        _set_job(job_id, {"progress": pct, "message": message})

    try:
        results, error = collect_items(
            params["selected_source"],
            params["selected_language"],
            params["selected_type"],
            params["keyword"],
            params["limit"],
            params["history_pages"],
            parse_article_html=params["parse_article_html"],
            include_archive=params["include_archive"],
            fill_with_general=params["fill_with_general"],
            min_per_source=params["min_per_source"],
            progress_callback=progress,
        )
        _set_job(
            job_id,
            {
                "status": "done",
                "progress": 100,
                "message": "완료",
                "error": error,
                "results": results,
            },
        )
    except Exception as exc:  # pragma: no cover
        _set_job(
            job_id,
            {
                "status": "failed",
                "progress": 100,
                "message": "실패",
                "error": f"크롤링 실패: {exc}",
                "results": [],
            },
        )


@app.route("/", methods=["GET", "POST"])
def index():
    enabled_sources = get_enabled_sources()
    source_keys = [ALL_SOURCES_KEY, *list(enabled_sources.keys())]
    selected_source = source_keys[0]
    selected_language = ALL_LANGUAGES_KEY
    selected_type = ALL_TYPES_KEY
    limit = 12
    keyword = ""
    history_pages = 3
    parse_article_html = True
    include_archive = True
    fill_with_general = True
    min_per_source = 1
    error = ""
    results: list[dict] = []

    if request.method == "POST":
        selected_source = request.form.get("source", source_keys[0])
        selected_language = request.form.get("language", ALL_LANGUAGES_KEY)
        selected_type = request.form.get("source_type", ALL_TYPES_KEY)
        keyword = (request.form.get("keyword", "") or "").strip()
        parse_article_html = True
        include_archive = request.form.get("include_archive") == "1"
        fill_with_general = request.form.get("fill_with_general") == "1"
        try:
            min_per_source = int(request.form.get("min_per_source", "1"))
        except ValueError:
            min_per_source = 1
        min_per_source = max(0, min(10, min_per_source))
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
            selected_type,
            keyword,
            limit,
            history_pages,
            parse_article_html=parse_article_html,
            include_archive=include_archive,
            fill_with_general=fill_with_general,
            min_per_source=min_per_source,
        )

    return render_template(
        "index.html",
        sources=enabled_sources,
        language_options=LANGUAGE_OPTIONS,
        source_type_options=SOURCE_TYPE_OPTIONS,
        selected_source=selected_source,
        selected_language=selected_language,
        selected_type=selected_type,
        limit=limit,
        keyword=keyword,
        history_pages=history_pages,
        parse_article_html=parse_article_html,
        include_archive=include_archive,
        fill_with_general=fill_with_general,
        min_per_source=min_per_source,
        error=error,
        results=results,
    )


@app.route("/crawl/start", methods=["POST"])
def crawl_start():
    enabled_sources = get_enabled_sources()
    source_keys = [ALL_SOURCES_KEY, *list(enabled_sources.keys())]
    selected_source = request.form.get("source", source_keys[0])
    selected_language = request.form.get("language", ALL_LANGUAGES_KEY)
    selected_type = request.form.get("source_type", ALL_TYPES_KEY)
    keyword = (request.form.get("keyword", "") or "").strip()
    parse_article_html = True
    include_archive = request.form.get("include_archive") == "1"
    fill_with_general = request.form.get("fill_with_general") == "1"
    try:
        min_per_source = int(request.form.get("min_per_source", "1"))
    except ValueError:
        min_per_source = 1
    min_per_source = max(0, min(10, min_per_source))

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
    signature = build_query_signature(
        selected_source=selected_source,
        selected_language=selected_language,
        selected_type=selected_type,
        keyword=keyword,
        limit=limit,
        history_pages=history_pages,
        include_archive=include_archive,
        fill_with_general=fill_with_general,
        min_per_source=min_per_source,
    )

    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        {
            "status": "running",
            "progress": 1,
            "message": "작업 시작",
            "error": "",
            "results": [],
            "signature": signature,
        },
    )
    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id,
            {
                "selected_source": selected_source,
                "selected_language": selected_language,
                "selected_type": selected_type,
                "keyword": keyword,
                "limit": limit,
                "history_pages": history_pages,
                "parse_article_html": parse_article_html,
                "include_archive": include_archive,
                "fill_with_general": fill_with_general,
                "min_per_source": min_per_source,
            },
        ),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/crawl/status/<job_id>", methods=["GET"])
def crawl_status(job_id: str):
    with JOBS_LOCK:
        job = CRAWL_JOBS.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


@app.route("/health/sources", methods=["GET"])
def health_sources():
    run = request.args.get("run", "0") == "1"
    timeout = int(request.args.get("timeout", "6"))
    timeout = max(2, min(15, timeout))
    enabled_sources = get_enabled_sources()
    total_enabled = len(enabled_sources)
    limit = int(request.args.get("limit", str(total_enabled if total_enabled else 1)))
    limit = max(1, min(total_enabled if total_enabled else 1, limit))

    rows: list[dict] = []
    summary = {"ok": 0, "fail": 0, "total": limit}
    selected = list(enabled_sources.values())[:limit]

    if run:
        headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
        for source in selected:
            status = "FAIL"
            detail = ""
            try:
                resp = requests.get(source.feed_url, timeout=(5, timeout), headers=headers)
                resp.raise_for_status()
                if source.source_type == "rss":
                    parse_feed(resp.content, 1)
                status = "OK"
                detail = f"HTTP {resp.status_code}"
            except Exception as exc:  # pragma: no cover
                detail = exc.__class__.__name__

            rows.append(
                {
                    "key": source.key,
                    "language": source.language,
                    "name": source.name,
                    "feed_url": source.feed_url,
                    "status": status,
                    "detail": detail,
                }
            )
            if status == "OK":
                summary["ok"] += 1
            else:
                summary["fail"] += 1

    return render_template(
        "health_sources.html",
        run=run,
        timeout=timeout,
        limit=limit,
        rows=rows,
        summary=summary,
        disabled_count=len(DISABLED_SOURCES),
    )


@app.route("/health/sources/disable_failed", methods=["POST"])
def health_disable_failed():
    timeout = int(request.form.get("timeout", "6"))
    timeout = max(2, min(15, timeout))
    limit = int(request.form.get("limit", str(len(get_enabled_sources()) or 1)))
    enabled_sources = get_enabled_sources()
    limit = max(1, min(len(enabled_sources) if enabled_sources else 1, limit))
    selected = list(enabled_sources.values())[:limit]
    headers = {"User-Agent": "GovNewsCrawler/1.0 (+https://example.local)"}
    failed: list[str] = []

    for source in selected:
        try:
            resp = requests.get(source.feed_url, timeout=(5, timeout), headers=headers)
            resp.raise_for_status()
            if source.source_type == "rss":
                parse_feed(resp.content, 1)
        except Exception:  # pragma: no cover
            failed.append(source.key)

    for key in failed:
        DISABLED_SOURCES.add(key)

    return redirect(url_for("health_sources", run=1, timeout=timeout, limit=limit))


@app.route("/health/sources/enable_all", methods=["POST"])
def health_enable_all():
    DISABLED_SOURCES.clear()
    return redirect(url_for("health_sources"))


@app.route("/export", methods=["POST"])
def export_excel():
    enabled_sources = get_enabled_sources()
    source_keys = [ALL_SOURCES_KEY, *list(enabled_sources.keys())]
    selected_source = request.form.get("source", source_keys[0])
    selected_language = request.form.get("language", ALL_LANGUAGES_KEY)
    selected_type = request.form.get("source_type", ALL_TYPES_KEY)
    keyword = (request.form.get("keyword", "") or "").strip()
    parse_article_html = True
    include_archive = request.form.get("include_archive") == "1"
    fill_with_general = request.form.get("fill_with_general") == "1"
    try:
        min_per_source = int(request.form.get("min_per_source", "1"))
    except ValueError:
        min_per_source = 1
    min_per_source = max(0, min(10, min_per_source))
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
    signature = build_query_signature(
        selected_source=selected_source,
        selected_language=selected_language,
        selected_type=selected_type,
        keyword=keyword,
        limit=limit,
        history_pages=history_pages,
        include_archive=include_archive,
        fill_with_general=fill_with_general,
        min_per_source=min_per_source,
    )

    latest_job_id = (request.form.get("latest_job_id", "") or "").strip()
    results: list[dict] = []
    error = ""

    if latest_job_id:
        with JOBS_LOCK:
            job = CRAWL_JOBS.get(latest_job_id, {})
        if job.get("status") == "done" and job.get("signature") == signature:
            job_results = job.get("results") or []
            # Respect current requested limit while avoiding re-crawl.
            results = list(job_results)[:limit]

    if not results:
        results, error = collect_items(
            selected_source,
            selected_language,
            selected_type,
            keyword,
            limit,
            history_pages,
            parse_article_html=parse_article_html,
            include_archive=include_archive,
            fill_with_general=fill_with_general,
            min_per_source=min_per_source,
        )
    if error and not results:
        return render_template(
            "index.html",
            sources=enabled_sources,
            language_options=LANGUAGE_OPTIONS,
            source_type_options=SOURCE_TYPE_OPTIONS,
            selected_source=selected_source,
            selected_language=selected_language,
            selected_type=selected_type,
            limit=limit,
            keyword=keyword,
            history_pages=history_pages,
            parse_article_html=parse_article_html,
            include_archive=include_archive,
            fill_with_general=fill_with_general,
            min_per_source=min_per_source,
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
        if selected_source in enabled_sources:
            source = enabled_sources[selected_source]
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
