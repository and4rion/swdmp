#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

COOKIE_ENV_VAR = "SHAREWOOD_COOKIE_HEADER"
DEFAULT_BASE_URL = "https://www.sharewood.tv"


def cookie_header() -> str:
    value = os.getenv(COOKIE_ENV_VAR, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {COOKIE_ENV_VAR}. Export your raw Cookie header before running."
        )
    return value


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def fetch_page(session: requests.Session, url: str, retries: int) -> requests.Response:
    attempt = 0
    while True:
        try:
            response = session.get(url, timeout=30, allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(min(30, 2**attempt))
                attempt += 1
                continue
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(min(30, 2**attempt))
            attempt += 1


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


def text_of(node) -> str | None:
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    return text if text else None


def extract_info_hash(soup: BeautifulSoup) -> str | None:
    label_pattern = re.compile(r"^\s*info\s*hash\s*$", flags=re.I)
    for strong in soup.select("tr td strong"):
        if not label_pattern.match(strong.get_text(" ", strip=True)):
            continue
        row = strong.find_parent("tr")
        if row is None:
            continue
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        value = normalize_ws(cells[1].get_text(" ", strip=True))
        if value:
            return value
    return None


def extract_nfo(soup: BeautifulSoup) -> str | None:
    # Sharewood detail page usually stores NFO/MediaInfo in this code block.
    node = soup.select_one("div.slidingDiv pre.decoda-code code")
    if node is None:
        node = soup.select_one("pre.decoda-code code")
    if node is None:
        return None
    value = node.get_text("\n", strip=False)
    value = value.strip("\n")
    return value or None


def extract_presentation_html(soup: BeautifulSoup) -> str | None:
    node = soup.select_one("div.panel-body.prez-body")
    if node is None:
        return None
    value = "".join(str(child) for child in node.contents).strip()
    return value or None


def extract_presentation_text(soup: BeautifulSoup) -> str | None:
    node = soup.select_one("div.panel-body.prez-body")
    if node is None:
        return None
    value = node.get_text("\n", strip=True).strip()
    return value or None


def extract_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.select_one("h1")
    title = text_of(h1)
    if title:
        return title
    if soup.title:
        return normalize_ws(soup.title.get_text(" ", strip=True))
    return None


def parse_detail(soup: BeautifulSoup) -> dict[str, str | None]:
    return {
        "title": extract_title(soup),
        "info_hash": extract_info_hash(soup),
        "nfo": extract_nfo(soup),
        "presentation_html": extract_presentation_html(soup),
        "presentation_text": extract_presentation_text(soup),
    }


def load_listing_details_urls(path: Path) -> list[tuple[str | None, str]]:
    out: list[tuple[str | None, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            details_url = data.get("details_url")
            if not isinstance(details_url, str) or not details_url:
                continue
            torrent_id = data.get("torrent_id")
            if not isinstance(torrent_id, str):
                torrent_id = None
            out.append((torrent_id, details_url))
    return out


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as f:
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
                done.add(torrent_id)
    return done


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Sharewood torrent detail pages."
    )
    parser.add_argument(
        "--in",
        dest="input_jsonl",
        default="output/torrents.jsonl",
        help="Input listing JSONL containing details_url",
    )
    parser.add_argument(
        "--out",
        dest="output_jsonl",
        default="output/torrent_details.jsonl",
        help="Output JSONL for enriched details",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Tracker base URL")
    parser.add_argument(
        "--proxy-base",
        default=None,
        help="Use local proxy base URL (example: http://127.0.0.1:8787)",
    )
    parser.add_argument(
        "--single-url",
        default=None,
        help="Scrape one detail URL only (for testing)",
    )
    parser.add_argument(
        "--single-id",
        default=None,
        help="Optional torrent id paired with --single-url",
    )
    parser.add_argument("--delay", type=float, default=0.8, help="Delay between pages")
    parser.add_argument(
        "--retries", type=int, default=3, help="Retries for transient errors"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Process only first N entries"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip torrent IDs already present in output file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    out_path = Path(args.output_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.single_url:
        items: list[tuple[str | None, str]] = [(args.single_id, args.single_url)]
    else:
        input_path = Path(args.input_jsonl)
        if not input_path.exists():
            raise RuntimeError(f"Input file not found: {input_path}")
        items = load_listing_details_urls(input_path)

    if args.limit > 0:
        items = items[: args.limit]

    done_ids = load_done_ids(out_path) if args.resume else set()

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

    using_proxy = bool(args.proxy_base)
    if not using_proxy:
        session.headers["Cookie"] = cookie_header()

    total = len(items)
    done = 0
    skipped = 0

    print(
        f"Starting detail scrape for {total} items (proxy={using_proxy}, resume={args.resume})"
    )

    with out_path.open("a", encoding="utf-8") as out_file:
        for idx, (torrent_id, details_url) in enumerate(items, start=1):
            if torrent_id and torrent_id in done_ids:
                skipped += 1
                continue

            absolute_url = urljoin(args.base_url + "/", details_url)
            fetch_url = (
                wrap_proxy_url(args.proxy_base, absolute_url)
                if using_proxy
                else absolute_url
            )

            try:
                response = fetch_page(session, fetch_url, retries=args.retries)
            except requests.RequestException as exc:
                print(f"[{idx}/{total}] fetch failed for {details_url}: {exc}")
                continue

            if "text/html" not in response.headers.get("Content-Type", "").lower():
                print(f"[{idx}/{total}] non-HTML response for {details_url}")
                continue

            soup = BeautifulSoup(response.text, "lxml")
            extracted = parse_detail(soup)
            record = {
                "torrent_id": torrent_id,
                "details_url": absolute_url,
                "title": extracted["title"],
                "info_hash": extracted["info_hash"],
                "nfo": extracted["nfo"],
                "presentation_html": extracted["presentation_html"],
                "presentation_text": extracted["presentation_text"],
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            out_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            done += 1
            if torrent_id:
                done_ids.add(torrent_id)

            hash_preview = (record["info_hash"] or "none")[:12]
            print(f"[{idx}/{total}] ok id={torrent_id or '-'} hash={hash_preview}...")

            if idx < total:
                time.sleep(max(0.0, args.delay))

    print(f"Done. written={done}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
