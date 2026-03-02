# qBittorrent Errored Torrent Curer

Small standalone script to fix errored torrents in qBittorrent by reusing the save location of cross-seeded torrents.

Matching strategy is intentionally strict and safe:
- only torrents with identical file lists match
- file list fingerprint is built from sorted `(relative_path, size)` pairs
- no fuzzy matching by filename-only or size-only

## Requirements

- Python 3.9+
- `requests`
- qBittorrent Web UI/API enabled

Install dependency:

```bash
python3 -m pip install requests
```

## Usage

Dry-run (default):

```bash
python3 qbt_cure_errored.py \
  --base-url "http://127.0.0.1:8080" \
  --username "admin" \
  --password "yourpass"
```

Apply fixes:

```bash
python3 qbt_cure_errored.py \
  --base-url "http://127.0.0.1:8080" \
  --username "admin" \
  --password "yourpass" \
  --apply
```

## Environment variables

You can set credentials and URL with env vars instead of CLI flags:

- `QBT_BASE_URL`
- `QBT_USERNAME`
- `QBT_PASSWORD`

Example:

```bash
export QBT_BASE_URL="http://127.0.0.1:8080"
export QBT_USERNAME="admin"
export QBT_PASSWORD="yourpass"
python3 qbt_cure_errored.py
```

## Notes

- Script runs in dry-run mode unless `--apply` is provided.
- By default, it skips ambiguous matches (multiple donors with same fingerprint).
- Use `--allow-ambiguous-match` only if you explicitly want first-match behavior.
