#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import requests
from bs4 import BeautifulSoup

COOKIE_ENV_VAR = "SHAREWOOD_COOKIE_HEADER"
DEFAULT_BASE_URL = "https://www.sharewood.tv"
DEFAULT_PATH_TEMPLATE = (
    "/filterTorrents?_token={token}&search=&description=&uploader=&tags="
    "&sorting=created_at&direction=desc&page={page}&qty=100"
)
DEFAULT_BOOTSTRAP_PATH = "/torrents"

SIZE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b", re.I)
INT_RE = re.compile(r"\b\d+\b")

HEADER_SYNONYMS = {
    "title": {"title", "nom", "name", "torrent"},
    "category": {"category", "cat", "categorie", "type", "genre"},
    "size": {"size", "taille", "poids"},
    "seeders": {"seed", "seeders", "seeds"},
    "leechers": {"leech", "leechers", "leechers"},
    "uploaded": {"uploaded", "date", "ajoute", "ajout", "added", "time"},
}

CSV_FIELDNAMES = [
    "torrent_id",
    "title",
    "details_url",
    "category",
    "subcategory",
    "size",
    "seeders",
    "leechers",
    "uploaded",
    "uploaded_relative",
    "page",
    "fetched_at",
]


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def text_norm(text: str) -> str:
    return normalize_ws(text).lower()


def cookie_header() -> str:
    value = os.getenv(COOKIE_ENV_VAR, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {COOKIE_ENV_VAR}. Export your raw Cookie header before running."
        )
    return value


def parse_cookie_header(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw.split(";"):
        chunk = item.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def token_from_cookie(raw_cookie_header: str) -> str | None:
    cookies = parse_cookie_header(raw_cookie_header)
    for name in ("XSRF-TOKEN", "csrf_token", "CSRF-TOKEN"):
        if name in cookies and cookies[name]:
            return unquote(cookies[name])
    return None


def token_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    meta = soup.find("meta", attrs={"name": "csrf-token"})
    if meta and meta.get("content"):
        return str(meta["content"])

    hidden = soup.find("input", attrs={"name": "_token"})
    if hidden and hidden.get("value"):
        return str(hidden["value"])

    return None


def resolve_token(
    session: requests.Session,
    base_url: str,
    cli_token: str | None,
    raw_cookie_header: str,
    bootstrap_path: str,
) -> str | None:
    if cli_token:
        return cli_token

    token = token_from_cookie(raw_cookie_header)
    if token:
        return token

    bootstrap_url = urljoin(base_url + "/", bootstrap_path)
    try:
        response = fetch_page(session, bootstrap_url, retries=2)
    except requests.RequestException:
        return None

    if "text/html" not in response.headers.get("Content-Type", "").lower():
        return None
    return token_from_html(response.text)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def extract_torrent_id(href: str) -> str | None:
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)

    for key in ("id", "torrent", "tid"):
        values = qs.get(key)
        if values and values[0].isdigit():
            return values[0]

    path_digits = re.findall(
        r"(?:torrent|details|download|dl)[^\d]*(\d+)", parsed.path, flags=re.I
    )
    if path_digits:
        return path_digits[0]

    trailing_digits = re.findall(r"/(\d+)(?:\D*$|$)", parsed.path)
    if trailing_digits:
        return trailing_digits[-1]

    return None


def is_detail_link(href: str) -> bool:
    lowered = href.lower()
    if any(token in lowered for token in ("details", "torrent", "view", "id=")):
        if ".torrent" in lowered:
            return False
        return True
    return False


def to_absolute(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def unwrap_proxy_href(href: str) -> str:
    parsed = urlparse(href)
    if parsed.path != "/proxy":
        return href
    qs = parse_qs(parsed.query)
    values = qs.get("path")
    if not values:
        return href
    return unquote(values[0])


def parse_int(text: str) -> int | None:
    m = INT_RE.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def parse_size(text: str) -> str | None:
    m = SIZE_RE.search(text)
    return m.group(0) if m else None


def subcategory_from_icon_src(src: str | None) -> str | None:
    if not src:
        return None
    clean_src = unwrap_proxy_href(src)
    path = urlparse(clean_src).path
    name = Path(path).stem
    if not name:
        return None
    normalized = re.sub(r"[_-]+", " ", name)
    normalized = normalize_ws(normalized)
    return normalized or None


def parse_relative_uploaded_to_iso(text: str, reference: datetime) -> str | None:
    if not text:
        return None

    s = text_norm(text).replace("’", "'")

    if any(token in s for token in ("a l'instant", "à l'instant", "quelques secondes")):
        return reference.isoformat()

    if "hier" in s:
        return (reference - timedelta(days=1)).isoformat()

    patterns = [
        (r"il y a\s+(\d+)\s+minute", timedelta(minutes=1)),
        (r"il y a\s+(\d+)\s+heure", timedelta(hours=1)),
        (r"il y a\s+(\d+)\s+jour", timedelta(days=1)),
        (r"il y a\s+(\d+)\s+semaine", timedelta(weeks=1)),
        (r"il y a\s+(\d+)\s+mois", timedelta(days=30)),
        (r"il y a\s+(\d+)\s+an", timedelta(days=365)),
        (r"(\d+)\s+minutes?\s+ago", timedelta(minutes=1)),
        (r"(\d+)\s+hours?\s+ago", timedelta(hours=1)),
        (r"(\d+)\s+days?\s+ago", timedelta(days=1)),
    ]

    for pattern, unit_delta in patterns:
        match = re.search(pattern, s)
        if not match:
            continue
        amount = int(match.group(1))
        return (reference - (unit_delta * amount)).isoformat()

    return None


def choose_header_map(header_cells: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    normalized = [text_norm(h) for h in header_cells]
    for field, synonyms in HEADER_SYNONYMS.items():
        for idx, h in enumerate(normalized):
            if any(s in h for s in synonyms):
                mapped[field] = idx
                break
    return mapped


@dataclass
class ParsedRow:
    torrent_id: str | None
    title: str
    details_url: str | None
    category: str | None
    subcategory: str | None
    size: str | None
    seeders: int | None
    leechers: int | None
    uploaded: str | None
    source_row_text: list[str]


def parse_listing_div_rows(soup: BeautifulSoup, base_url: str) -> list[ParsedRow]:
    rows: list[ParsedRow] = []
    for row in soup.select("div.row.table-responsive-line"):
        link = row.select_one("a.view-torrent[href]")
        if link is None:
            continue

        details_href = unwrap_proxy_href(str(link.get("href", "")))
        details_url = to_absolute(base_url, details_href)

        torrent_id = None
        data_id = str(link.get("data-id", "")).strip()
        if data_id.isdigit():
            torrent_id = data_id
        if not torrent_id:
            torrent_id = extract_torrent_id(details_url or details_href)

        title = normalize_ws(link.get_text(" ", strip=True))
        if not title:
            continue

        category = None
        icon = row.select_one("img.torrent-icon")
        if icon is not None:
            original = normalize_ws(str(icon.get("data-original-title", "")))
            if original.lower().endswith(" torrent"):
                original = original[: -len(" torrent")].strip()
            category = original or None
        subcategory = subcategory_from_icon_src(icon.get("src") if icon else None)

        uploaded = None
        size = None
        seeders = None
        leechers = None

        detail_cols = row.select("div.col-md-2.col-detail")
        if len(detail_cols) >= 1:
            spans = detail_cols[0].select("span")
            if len(spans) >= 1:
                uploaded = normalize_ws(spans[0].get_text(" ", strip=True)) or None
            if len(spans) >= 2:
                size = parse_size(normalize_ws(spans[1].get_text(" ", strip=True)))

        if len(detail_cols) >= 2:
            slc = detail_cols[1].select("div.bouton-slc")
            if len(slc) >= 1:
                seeders = parse_int(normalize_ws(slc[0].get_text(" ", strip=True)))
            if len(slc) >= 2:
                leechers = parse_int(normalize_ws(slc[1].get_text(" ", strip=True)))

        rows.append(
            ParsedRow(
                torrent_id=torrent_id,
                title=title,
                details_url=details_url,
                category=category,
                subcategory=subcategory,
                size=size,
                seeders=seeders,
                leechers=leechers,
                uploaded=uploaded,
                source_row_text=[normalize_ws(row.get_text(" ", strip=True))],
            )
        )

    return rows


def parse_listing_table(soup: BeautifulSoup, base_url: str) -> list[ParsedRow]:
    div_rows = parse_listing_div_rows(soup, base_url)
    if div_rows:
        return div_rows

    tables = soup.find_all("table")
    if not tables:
        return []

    best_rows: list[ParsedRow] = []
    best_score = -1

    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 5:
            continue

        header_cells = [
            normalize_ws(c.get_text(" ", strip=True))
            for c in rows[0].find_all(["th", "td"])
        ]
        header_map = choose_header_map(header_cells) if header_cells else {}

        parsed_rows: list[ParsedRow] = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            cell_texts = [normalize_ws(c.get_text(" ", strip=True)) for c in cells]
            all_links = row.find_all("a", href=True)
            if not all_links:
                continue

            details = None
            for link in all_links:
                href = link.get("href", "")
                if is_detail_link(href):
                    details = link
                    break
            if details is None:
                details = all_links[0]

            details_href = unwrap_proxy_href(details.get("href", ""))
            details_url = to_absolute(base_url, details_href)
            torrent_id = extract_torrent_id(details_url or details_href)

            title = normalize_ws(details.get_text(" ", strip=True))
            if (
                not title
                and "title" in header_map
                and header_map["title"] < len(cell_texts)
            ):
                title = cell_texts[header_map["title"]]
            if not title:
                continue

            category = None
            if "category" in header_map and header_map["category"] < len(cell_texts):
                category = cell_texts[header_map["category"]] or None

            size = None
            if "size" in header_map and header_map["size"] < len(cell_texts):
                size = parse_size(cell_texts[header_map["size"]])
            if not size:
                for text in cell_texts:
                    size = parse_size(text)
                    if size:
                        break

            seeders = None
            if "seeders" in header_map and header_map["seeders"] < len(cell_texts):
                seeders = parse_int(cell_texts[header_map["seeders"]])

            leechers = None
            if "leechers" in header_map and header_map["leechers"] < len(cell_texts):
                leechers = parse_int(cell_texts[header_map["leechers"]])

            uploaded = None
            if "uploaded" in header_map and header_map["uploaded"] < len(cell_texts):
                uploaded = cell_texts[header_map["uploaded"]] or None

            parsed_rows.append(
                ParsedRow(
                    torrent_id=torrent_id,
                    title=title,
                    details_url=details_url,
                    category=category,
                    subcategory=None,
                    size=size,
                    seeders=seeders,
                    leechers=leechers,
                    uploaded=uploaded,
                    source_row_text=cell_texts,
                )
            )

        score = sum(1 for item in parsed_rows if item.torrent_id or item.details_url)
        if score > best_score:
            best_score = score
            best_rows = parsed_rows

    if best_rows:
        return best_rows

    fallback_rows: list[ParsedRow] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = unwrap_proxy_href(link.get("href", ""))
        if not is_detail_link(href):
            continue

        details_url = to_absolute(base_url, href)
        if not details_url or details_url in seen_urls:
            continue

        torrent_id = extract_torrent_id(details_url)
        if not torrent_id:
            continue

        title = normalize_ws(link.get_text(" ", strip=True))
        if not title:
            continue

        container = link.find_parent(["tr", "li", "article", "div"]) or link
        row_text = normalize_ws(container.get_text(" ", strip=True))
        size = parse_size(row_text)

        seen_urls.add(details_url)
        fallback_rows.append(
            ParsedRow(
                torrent_id=torrent_id,
                title=title,
                details_url=details_url,
                category=None,
                subcategory=None,
                size=size,
                seeders=None,
                leechers=None,
                uploaded=None,
                source_row_text=[row_text],
            )
        )

    return fallback_rows


def build_page_url(
    base_url: str,
    template: str,
    page: int,
    token: str | None,
) -> str:
    rendered = template.format(page=page, token=token or "")
    if rendered.startswith("http://") or rendered.startswith("https://"):
        return rendered
    return urljoin(base_url + "/", rendered)


def wrap_proxy_url(proxy_base: str, upstream_url_or_path: str) -> str:
    parsed = urlparse(upstream_url_or_path)
    if parsed.scheme in {"http", "https"}:
        path_and_query = parsed.path or "/"
        if parsed.query:
            path_and_query = f"{path_and_query}?{parsed.query}"
    else:
        path_and_query = upstream_url_or_path
    encoded = quote(path_and_query, safe="/?=&%:+,;-_.~")
    return f"{proxy_base.rstrip('/')}/proxy?path={encoded}"


def discover_template_via_proxy(
    session: requests.Session,
    proxy_base: str,
    bootstrap_path: str,
    retries: int,
) -> str:
    fallback_template = "/torrents?page={page}"

    def first_id_from_html(html: str) -> str | None:
        soup_local = BeautifulSoup(html, "lxml")
        first = soup_local.select_one("a.view-torrent[data-id]")
        if first is None:
            return None
        value = str(first.get("data-id", "")).strip()
        return value if value.isdigit() else None

    def first_id_for_template(
        candidate_template: str,
        page: int,
    ) -> tuple[str | None, str | None]:
        upstream = candidate_template.format(page=page)
        proxied = wrap_proxy_url(proxy_base, upstream)
        resp = fetch_page(session, proxied, retries=retries)
        if "text/html" not in resp.headers.get("Content-Type", "").lower():
            return None, resp.headers.get("X-Proxy-Upstream-URL")
        return first_id_from_html(resp.text), resp.headers.get("X-Proxy-Upstream-URL")

    def template_paginates(candidate_template: str) -> bool:
        id1, upstream1 = first_id_for_template(candidate_template, 1)
        id2, upstream2 = first_id_for_template(candidate_template, 2)
        if not (id1 and id2 and id1 != id2):
            return False

        if upstream2:
            parsed2 = urlparse(upstream2)
            q2 = parse_qs(parsed2.query)
            if "page" in q2 and q2["page"] and q2["page"][0] != "2":
                return False
            if "page" not in q2 and "page=" in candidate_template:
                return False

        return True

    if template_paginates(fallback_template):
        return fallback_template

    bootstrap_url = wrap_proxy_url(proxy_base, bootstrap_path)
    response = fetch_page(session, bootstrap_url, retries=retries)
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("Proxy bootstrap response is not HTML.")

    soup = BeautifulSoup(response.text, "lxml")
    iframe_src: str | None = None
    for iframe in soup.find_all("iframe", src=True):
        src = str(iframe.get("src", ""))
        original_src = unwrap_proxy_href(src)
        if "filterTorrents" in original_src:
            iframe_src = original_src
            break

    if not iframe_src:
        for link in soup.find_all("a", href=True):
            href = unwrap_proxy_href(str(link.get("href", "")))
            if "filterTorrents" in href:
                iframe_src = href
                break

    if not iframe_src:
        token = token_from_html(response.text)
        if token:
            return (
                f"/filterTorrents?_token={token}&search=&description=&uploader=&tags="
                "&sorting=created_at&direction=desc&page={page}&qty=100"
            )
        raise RuntimeError(
            "Could not discover filterTorrents URL or csrf token from bootstrap page."
        )

    parsed = urlparse(iframe_src)
    query = parse_qs(parsed.query, keep_blank_values=True)
    token = query.get("_token", [None])[0] or token_from_html(response.text)
    if token and "_token" not in query:
        query["_token"] = [token]
    if "page" not in query:
        raise RuntimeError("Discovered iframe URL has no page query parameter.")

    query["page"] = ["{page}"]
    rebuilt_query = urlencode(query, doseq=True).replace("%7Bpage%7D", "{page}")
    if parsed.path:
        iframe_template = f"{parsed.path}?{rebuilt_query}"
    else:
        iframe_template = f"/?{rebuilt_query}"

    if template_paginates(iframe_template):
        return iframe_template

    return iframe_template


def add_page_fallback(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "page" not in query:
        query["page"] = [str(page)]
        rebuilt = parsed._replace(query=urlencode(query, doseq=True))
        return urlunparse(rebuilt)
    return url


def fetch_page(session: requests.Session, url: str, retries: int) -> requests.Response:
    attempt = 0
    while True:
        try:
            response = session.get(url, timeout=30, allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                sleep_s = min(30, 2**attempt)
                time.sleep(sleep_s)
                attempt += 1
                continue
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt >= retries:
                raise
            sleep_s = min(30, 2**attempt)
            time.sleep(sleep_s)
            attempt += 1


def load_existing_ids(jsonl_path: Path) -> set[str]:
    seen: set[str] = set()
    if not jsonl_path.exists():
        return seen

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            torrent_id = data.get("torrent_id")
            if isinstance(torrent_id, str) and torrent_id:
                seen.add(torrent_id)
    return seen


def write_csv_header(path: Path) -> None:
    ensure_parent(path)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open("r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        expected = ",".join(CSV_FIELDNAMES)
        if first_line != expected:
            raise RuntimeError(
                f"Existing CSV header mismatch in {path}. Use a new --csv-out path or remove the file."
            )
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()


def append_csv_rows(path: Path, records: list[dict]) -> None:
    if not records:
        return
    write_csv_header(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writerows(records)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Sharewood listing pages.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Tracker base URL")
    parser.add_argument(
        "--path-template",
        default=DEFAULT_PATH_TEMPLATE,
        help="Listing path template, must include {page}; may include {token}",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="CSRF token override. If omitted and {token} is used, auto-resolve.",
    )
    parser.add_argument(
        "--bootstrap-path",
        default=DEFAULT_BOOTSTRAP_PATH,
        help="Page used to auto-resolve CSRF token when needed",
    )
    parser.add_argument(
        "--proxy-base",
        default=None,
        help="Use local proxy base URL (example: http://127.0.0.1:8787)",
    )
    parser.add_argument(
        "--discover-template",
        action="store_true",
        help="Discover listing template from iframe via proxy bootstrap page",
    )
    parser.add_argument("--start-page", type=int, default=1, help="First page to fetch")
    parser.add_argument("--end-page", type=int, default=1240, help="Last page to fetch")
    parser.add_argument(
        "--delay", type=float, default=1.5, help="Delay between pages (seconds)"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Retries for transient errors"
    )
    parser.add_argument(
        "--out",
        default="output/torrents.jsonl",
        help="JSONL output path (incremental append)",
    )
    parser.add_argument(
        "--csv-out", default="output/torrents.csv", help="CSV output path"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load existing JSONL and skip seen torrent IDs",
    )
    parser.add_argument(
        "--keep-raw-columns",
        action="store_true",
        help="Store row text columns under raw_columns in JSONL",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if "{page}" not in args.path_template:
        raise ValueError("--path-template must include {page}")
    if args.start_page > args.end_page:
        raise ValueError("--start-page must be <= --end-page")

    out_path = Path(args.out)
    csv_path = Path(args.csv_out)
    ensure_parent(out_path)

    seen_ids = load_existing_ids(out_path) if args.resume else set()

    using_proxy = bool(args.proxy_base)
    raw_cookie = cookie_header() if not using_proxy else ""

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    if not using_proxy:
        session.headers["Cookie"] = raw_cookie

    path_template = args.path_template
    if using_proxy and (
        args.discover_template or args.path_template == DEFAULT_PATH_TEMPLATE
    ):
        path_template = discover_template_via_proxy(
            session=session,
            proxy_base=args.proxy_base,
            bootstrap_path=args.bootstrap_path,
            retries=args.retries,
        )
        print(f"Discovered template from proxy iframe: {path_template}")

    total_written = 0
    total_seen = len(seen_ids)
    total_pages = args.end_page - args.start_page + 1

    resolved_token: str | None = None
    if "{token}" in path_template and not using_proxy:
        resolved_token = resolve_token(
            session=session,
            base_url=args.base_url,
            cli_token=args.token,
            raw_cookie_header=raw_cookie,
            bootstrap_path=args.bootstrap_path,
        )
        if not resolved_token:
            raise RuntimeError(
                "Could not resolve CSRF token. Pass it explicitly with --token."
            )
    if "{token}" in path_template and using_proxy:
        if args.token:
            resolved_token = args.token
        else:
            bootstrap_url = wrap_proxy_url(args.proxy_base, args.bootstrap_path)
            bootstrap_response = fetch_page(
                session, bootstrap_url, retries=args.retries
            )
            if (
                "text/html"
                in bootstrap_response.headers.get("Content-Type", "").lower()
            ):
                resolved_token = token_from_html(bootstrap_response.text)
        if not resolved_token:
            raise RuntimeError(
                "Could not resolve token in proxy mode. Use --discover-template or pass --token."
            )

    print(
        f"Starting scrape pages {args.start_page}..{args.end_page} ({total_pages} pages), "
        f"delay={args.delay}s, resume_seen={total_seen}"
    )

    for idx, page in enumerate(range(args.start_page, args.end_page + 1), start=1):
        page_url = build_page_url(
            args.base_url,
            path_template,
            page,
            resolved_token,
        )
        page_url = add_page_fallback(page_url, page)
        fetch_url = (
            wrap_proxy_url(args.proxy_base, page_url) if using_proxy else page_url
        )

        try:
            response = fetch_page(session, fetch_url, retries=args.retries)
        except requests.RequestException as exc:
            print(f"[page {page}] fetch failed: {exc}")
            continue

        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower():
            print(f"[page {page}] skipped non-HTML response: {content_type}")
            continue

        soup = BeautifulSoup(response.text, "lxml")
        rows = parse_listing_table(soup, args.base_url)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        jsonl_records: list[dict] = []
        csv_records: list[dict] = []

        for row in rows:
            unique_key = row.torrent_id or row.details_url
            if (
                isinstance(unique_key, str)
                and row.torrent_id
                and row.torrent_id in seen_ids
            ):
                continue

            record = {
                "torrent_id": row.torrent_id,
                "title": row.title,
                "details_url": row.details_url,
                "category": row.category,
                "subcategory": row.subcategory,
                "size": row.size,
                "seeders": row.seeders,
                "leechers": row.leechers,
                "uploaded": parse_relative_uploaded_to_iso(row.uploaded or "", now_dt)
                or row.uploaded,
                "uploaded_relative": row.uploaded,
                "page": page,
                "fetched_at": now,
            }
            if args.keep_raw_columns:
                record["raw_columns"] = row.source_row_text

            jsonl_records.append(record)
            csv_records.append(
                {
                    "torrent_id": row.torrent_id,
                    "title": row.title,
                    "details_url": row.details_url,
                    "category": row.category,
                    "subcategory": row.subcategory,
                    "size": row.size,
                    "seeders": row.seeders,
                    "leechers": row.leechers,
                    "uploaded": parse_relative_uploaded_to_iso(
                        row.uploaded or "", now_dt
                    )
                    or row.uploaded,
                    "uploaded_relative": row.uploaded,
                    "page": page,
                    "fetched_at": now,
                }
            )

            if row.torrent_id:
                seen_ids.add(row.torrent_id)

        if jsonl_records:
            with out_path.open("a", encoding="utf-8") as f:
                for record in jsonl_records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

            append_csv_rows(csv_path, csv_records)
            total_written += len(jsonl_records)

        print(
            f"[page {page}] parsed={len(rows)} written={len(jsonl_records)} "
            f"progress={idx}/{total_pages}"
        )

        if page < args.end_page:
            time.sleep(max(0.0, args.delay))

    print(
        f"Done. New records written: {total_written}. Total seen IDs: {len(seen_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
