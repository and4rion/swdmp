#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from typing import Mapping, Sequence
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, request

TARGET_HOST = "www.sharewood.tv"
TARGET_BASE = f"https://{TARGET_HOST}"
COOKIE_ENV_VAR = "SHAREWOOD_COOKIE_HEADER"

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _cookie_header() -> str:
    value = os.getenv(COOKIE_ENV_VAR, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {COOKIE_ENV_VAR}. Export your raw Cookie header before running."
        )
    return value


def _allowed_target(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == TARGET_HOST


def _normalize_target(path_value: str | None) -> str:
    if not path_value:
        return TARGET_BASE + "/"

    raw = path_value.strip()
    if raw.startswith(("javascript:", "data:")):
        raise ValueError("Blocked URL scheme")

    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        if not _allowed_target(raw):
            raise ValueError("Blocked upstream host")
        return raw

    if raw.startswith("//"):
        absolute = f"https:{raw}"
        if not _allowed_target(absolute):
            raise ValueError("Blocked upstream host")
        return absolute

    absolute = urljoin(TARGET_BASE + "/", raw)
    if not _allowed_target(absolute):
        raise ValueError("Blocked upstream host")
    return absolute


def _proxy_url_for_target(target_url: str) -> str:
    parsed = urlparse(target_url)
    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"
    encoded = quote(path_and_query, safe="/?=&%:+,;-_.~")
    return f"/proxy?path={encoded}"


def _rewrite_url_attr(value: str, base_url: str) -> str:
    v = value.strip()
    if not v:
        return value
    if v.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return value

    target = urljoin(base_url, v)
    if not _allowed_target(target):
        return value
    return _proxy_url_for_target(target)


def _rewrite_html_links(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag_name, attr in (
        ("a", "href"),
        ("link", "href"),
        ("img", "src"),
        ("script", "src"),
    ):
        for tag in soup.find_all(tag_name):
            current = tag.get(attr)
            if current:
                tag[attr] = _rewrite_url_attr(current, base_url)

    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        if method == "get":
            action = form.get("action") or base_url
            form["action"] = _rewrite_url_attr(action, base_url)

    return str(soup)


def _filtered_headers(
    upstream_headers: Mapping[str, str],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in upstream_headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered in {"content-length", "content-encoding"}:
            continue
        out[key] = value
    return out


def _upstream_request(method: str, target_url: str) -> requests.Response:
    headers = {
        "Cookie": _cookie_header(),
        "User-Agent": request.headers.get("User-Agent", DEFAULT_UA),
        "Accept": request.headers.get(
            "Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": request.headers.get("Accept-Language", "en-US,en;q=0.9"),
    }

    return requests.request(
        method=method,
        url=target_url,
        headers=headers,
        timeout=30,
        allow_redirects=True,
    )


def _auth_hint(upstream: requests.Response) -> str | None:
    final_path = urlparse(upstream.url).path.lower()
    if upstream.status_code in {401, 403}:
        return f"upstream returned {upstream.status_code}"
    if any(token in final_path for token in ("login", "signin", "auth")):
        return f"redirected to auth page: {final_path}"
    return None


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> Response:
        return jsonify(
            {
                "service": "sharewood-local-proxy",
                "target_host": TARGET_HOST,
                "proxy_endpoint": "/proxy?path=/",
                "cookie_env": COOKIE_ENV_VAR,
            }
        )

    @app.route("/proxy", methods=["GET", "HEAD"])
    def proxy() -> Response:
        try:
            target = _normalize_target(request.args.get("path", "/"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            upstream = _upstream_request(request.method, target)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        except requests.RequestException as exc:
            return jsonify({"error": f"Upstream request failed: {exc}"}), 502

        headers = _filtered_headers(upstream.headers)
        headers["X-Proxy-Upstream-URL"] = upstream.url
        headers["X-Proxy-Upstream-Status"] = str(upstream.status_code)

        auth_hint = _auth_hint(upstream)
        if auth_hint:
            headers["X-Proxy-Auth-Hint"] = auth_hint

        content_type = upstream.headers.get("Content-Type", "")
        if request.method == "HEAD":
            return Response(status=upstream.status_code, headers=headers)

        if "text/html" in content_type.lower():
            rewritten = _rewrite_html_links(upstream.text, upstream.url)
            return Response(rewritten, status=upstream.status_code, headers=headers)

        return Response(upstream.content, status=upstream.status_code, headers=headers)

    @app.get("/analyze")
    def analyze() -> Response:
        try:
            target = _normalize_target(request.args.get("path", "/"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            upstream = _upstream_request("GET", target)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        except requests.RequestException as exc:
            return jsonify({"error": f"Upstream request failed: {exc}"}), 502

        content_type = upstream.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower():
            return jsonify(
                {
                    "url": upstream.url,
                    "status": upstream.status_code,
                    "content_type": content_type,
                    "error": "Not an HTML page",
                }
            )

        soup = BeautifulSoup(upstream.text, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else None

        table_count = len(soup.find_all("table"))
        row_count = len(soup.find_all("tr"))
        link_count = len(soup.find_all("a"))

        torrent_like_links = 0
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].lower()
            if any(
                token in href for token in ("torrent", "details", "id=", "download")
            ):
                torrent_like_links += 1

        return jsonify(
            {
                "url": upstream.url,
                "status": upstream.status_code,
                "title": title,
                "table_count": table_count,
                "row_count": row_count,
                "link_count": link_count,
                "torrent_like_link_count": torrent_like_links,
                "auth_hint": _auth_hint(upstream),
            }
        )

    return app


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local cookie-backed proxy for www.sharewood.tv"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8787, help="Bind port (default: 8787)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
