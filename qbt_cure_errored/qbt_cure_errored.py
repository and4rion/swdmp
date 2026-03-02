#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import sys
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 20


@dataclass
class Torrent:
    hash: str
    name: str
    state: str
    save_path: str


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cure errored qBittorrent torrents by reusing location from cross-seeded "
            "torrents with strict file path+size matching."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("QBT_BASE_URL", DEFAULT_BASE_URL),
        help="qBittorrent Web UI base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("QBT_USERNAME"),
        help="qBittorrent username (or QBT_USERNAME env var)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("QBT_PASSWORD"),
        help="qBittorrent password (or QBT_PASSWORD env var)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply fixes. Without this flag, script runs in dry-run mode.",
    )
    parser.add_argument(
        "--require-unique-match",
        action="store_true",
        default=True,
        help="Only fix when exactly one matching donor torrent exists (default: enabled)",
    )
    parser.add_argument(
        "--allow-ambiguous-match",
        action="store_true",
        help="Allow ambiguous matches by picking the first donor (not recommended)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--ambiguous-details-limit",
        type=int,
        default=10,
        help="How many donor rows to print for ambiguous matches (default: %(default)s)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colored output",
    )
    return parser.parse_args()


class QBittorrentClient:
    def __init__(
        self, base_url: str, username: str, password: str, timeout: int
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self._login(username, password)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _login(self, username: str, password: str) -> None:
        response = self.session.post(
            self._url("/api/v2/auth/login"),
            data={"username": username, "password": password},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.text.strip() != "Ok.":
            raise RuntimeError(f"Login failed: {response.text.strip()}")

    def list_torrents(self, filter_name: str = "all") -> list[Torrent]:
        response = self.session.get(
            self._url("/api/v2/torrents/info"),
            params={"filter": filter_name},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            Torrent(
                hash=item.get("hash", ""),
                name=item.get("name", ""),
                state=item.get("state", ""),
                save_path=item.get("save_path", ""),
            )
            for item in payload
            if item.get("hash")
        ]

    def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        response = self.session.get(
            self._url("/api/v2/torrents/files"),
            params={"hash": torrent_hash},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected files payload for {torrent_hash}")
        return data

    def set_location(self, torrent_hash: str, location: str) -> None:
        response = self.session.post(
            self._url("/api/v2/torrents/setLocation"),
            data={"hashes": torrent_hash, "location": location},
            timeout=self.timeout,
        )
        response.raise_for_status()


def normalize_rel_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip()
    normalized = posixpath.normpath(cleaned)
    if normalized in {".", ""}:
        return ""
    return normalized.lstrip("/")


def build_fingerprint(files: list[dict[str, Any]]) -> str:
    pairs: list[tuple[str, int]] = []
    for item in files:
        name = normalize_rel_path(str(item.get("name", "")))
        if not name:
            continue
        size = int(item.get("size", 0))
        pairs.append((name, size))

    pairs.sort(key=lambda x: x[0])
    serialized = json.dumps(pairs, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def short_hash(value: str) -> str:
    return value[:12]


def normalized_dir(path: str) -> str:
    return path.rstrip("/\\")


def supports_color(disabled: bool) -> bool:
    if disabled:
        return False
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(enabled: bool, text: str, *styles: str) -> str:
    if not enabled or not styles:
        return text
    return "".join(styles) + text + Colors.RESET


def main() -> int:
    args = parse_args()
    use_color = supports_color(args.no_color)

    if args.allow_ambiguous_match:
        args.require_unique_match = False

    if not args.username or not args.password:
        raise SystemExit(
            "Missing credentials. Provide --username/--password or set QBT_USERNAME and QBT_PASSWORD."
        )

    client = QBittorrentClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        timeout=args.timeout,
    )

    errored = client.list_torrents("errored")
    all_torrents = client.list_torrents("all")
    healthy = [t for t in all_torrents if t.hash not in {e.hash for e in errored}]

    print(
        paint(
            use_color, f"Found {len(errored)} errored torrents", Colors.BOLD, Colors.RED
        )
    )
    print(
        paint(
            use_color,
            f"Found {len(healthy)} candidate donor torrents",
            Colors.BOLD,
            Colors.CYAN,
        )
    )

    donor_index: dict[str, list[Torrent]] = {}
    for donor in healthy:
        try:
            donor_files = client.torrent_files(donor.hash)
            donor_fp = build_fingerprint(donor_files)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip donor] {short_hash(donor.hash)} {donor.name}: {exc}")
            continue
        donor_index.setdefault(donor_fp, []).append(donor)

    proposed = 0
    applied = 0
    skipped_no_match = 0
    skipped_ambiguous = 0
    unchanged = 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    mode_color = Colors.GREEN if args.apply else Colors.YELLOW
    print(f"Mode: {paint(use_color, mode, Colors.BOLD, mode_color)}")

    for bad in errored:
        try:
            bad_files = client.torrent_files(bad.hash)
            bad_fp = build_fingerprint(bad_files)
        except Exception as exc:  # noqa: BLE001
            print(
                paint(
                    use_color,
                    f"[skip error] {short_hash(bad.hash)} {bad.name}: unable to read files ({exc})",
                    Colors.YELLOW,
                )
            )
            continue

        matches = donor_index.get(bad_fp, [])
        if not matches:
            skipped_no_match += 1
            print(
                paint(
                    use_color,
                    f"[no match] {short_hash(bad.hash)} {bad.name}",
                    Colors.YELLOW,
                )
            )
            continue

        distinct_locations = sorted(
            {normalized_dir(item.save_path) for item in matches if item.save_path}
        )

        if (
            args.require_unique_match
            and len(matches) != 1
            and len(distinct_locations) > 1
        ):
            skipped_ambiguous += 1
            print(
                paint(
                    use_color,
                    f"[ambiguous] {short_hash(bad.hash)} {bad.name}: "
                    f"{len(matches)} donors point to {len(distinct_locations)} different locations",
                    Colors.YELLOW,
                )
            )

            limit = max(0, args.ambiguous_details_limit)
            for idx, donor in enumerate(matches[:limit], start=1):
                print(
                    f"      {idx}. {short_hash(donor.hash)} state={donor.state or '-'}\n"
                    f"         name={donor.name}\n"
                    f"         path={donor.save_path or '-'}"
                )

            if len(matches) > limit:
                print(f"      ... {len(matches) - limit} more donor(s)")

            loc_preview = ", ".join(distinct_locations[:3])
            loc_suffix = " ..." if len(distinct_locations) > 3 else ""
            print(f"      locations={loc_preview}{loc_suffix}")
            continue

        donor = matches[0]
        target_location = donor.save_path

        if len(matches) > 1 and len(distinct_locations) == 1:
            print(
                paint(
                    use_color,
                    f"[multi-donor same path] {short_hash(bad.hash)} {bad.name}: "
                    f"{len(matches)} donors agree on {target_location}",
                    Colors.CYAN,
                )
            )

        if bad.save_path.rstrip("/") == target_location.rstrip("/"):
            unchanged += 1
            print(
                paint(
                    use_color,
                    f"[already set] {short_hash(bad.hash)} {bad.name}: {target_location}",
                    Colors.BLUE,
                )
            )
            continue

        proposed += 1
        print(
            paint(
                use_color,
                f"[fix] {short_hash(bad.hash)} {bad.name}",
                Colors.GREEN,
                Colors.BOLD,
            )
        )
        print(f"      donor={short_hash(donor.hash)} {donor.name}")
        print(f"      from={bad.save_path}")
        print(f"      to  ={target_location}")

        if args.apply:
            try:
                client.set_location(bad.hash, target_location)
                applied += 1
            except Exception as exc:  # noqa: BLE001
                print(paint(use_color, f"      apply failed: {exc}", Colors.RED))

    print(paint(use_color, "\nDone", Colors.BOLD))
    print(paint(use_color, f"- Proposed fixes: {proposed}", Colors.CYAN))
    print(paint(use_color, f"- Applied fixes: {applied}", Colors.GREEN))
    print(paint(use_color, f"- Skipped (no match): {skipped_no_match}", Colors.YELLOW))
    print(
        paint(
            use_color,
            f"- Skipped (ambiguous): {skipped_ambiguous}",
            Colors.YELLOW,
        )
    )
    print(paint(use_color, f"- Already correct: {unchanged}", Colors.BLUE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
