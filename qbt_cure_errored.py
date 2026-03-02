#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
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


def main() -> int:
    args = parse_args()

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

    print(f"Found {len(errored)} errored torrents")
    print(f"Found {len(healthy)} candidate donor torrents")

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
    print(f"Mode: {mode}")

    for bad in errored:
        try:
            bad_files = client.torrent_files(bad.hash)
            bad_fp = build_fingerprint(bad_files)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[skip error] {short_hash(bad.hash)} {bad.name}: unable to read files ({exc})"
            )
            continue

        matches = donor_index.get(bad_fp, [])
        if not matches:
            skipped_no_match += 1
            print(f"[no match] {short_hash(bad.hash)} {bad.name}")
            continue

        if args.require_unique_match and len(matches) != 1:
            skipped_ambiguous += 1
            hashes = ", ".join(short_hash(item.hash) for item in matches[:5])
            suffix = " ..." if len(matches) > 5 else ""
            print(
                f"[ambiguous] {short_hash(bad.hash)} {bad.name}: "
                f"{len(matches)} donors ({hashes}{suffix})"
            )
            continue

        donor = matches[0]
        target_location = donor.save_path

        if bad.save_path.rstrip("/") == target_location.rstrip("/"):
            unchanged += 1
            print(f"[already set] {short_hash(bad.hash)} {bad.name}: {target_location}")
            continue

        proposed += 1
        print(
            f"[fix] {short_hash(bad.hash)} {bad.name}\n"
            f"      donor={short_hash(donor.hash)} {donor.name}\n"
            f"      from={bad.save_path}\n"
            f"      to  ={target_location}"
        )

        if args.apply:
            try:
                client.set_location(bad.hash, target_location)
                applied += 1
            except Exception as exc:  # noqa: BLE001
                print(f"      apply failed: {exc}")

    print("\nDone")
    print(f"- Proposed fixes: {proposed}")
    print(f"- Applied fixes: {applied}")
    print(f"- Skipped (no match): {skipped_no_match}")
    print(f"- Skipped (ambiguous): {skipped_ambiguous}")
    print(f"- Already correct: {unchanged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
