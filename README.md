# Sharewood Listing Archiver

This workspace provides a safe process to archive torrent listings from `https://www.sharewood.tv` using a local cookie-backed proxy and then a BeautifulSoup scraper.

Current status: proxy is implemented in `proxy.py`.

## Requirements
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv)
- An active authenticated browser session on `www.sharewood.tv`

## Quick setup (uv + virtual environment)

```bash
uv venv .venv
source .venv/bin/activate
uv pip install flask requests beautifulsoup4 lxml
```

Optional: freeze dependencies once scripts are created.

```bash
uv pip freeze > requirements.txt
```

## Cookie setup (raw Cookie header)
1. Open browser DevTools on an authenticated Sharewood page.
2. In Network, select a normal page request to `www.sharewood.tv`.
3. Copy the full `Cookie` request header value.
4. Export it into your shell:

```bash
export SHAREWOOD_COOKIE_HEADER='cookie1=value1; cookie2=value2; ...'
```

Security notes:
- Do not commit this value.
- Do not share screenshots containing this header.
- Rotate cookie if leaked.

## Project structure

```text
.
├─ PLAN.md
├─ README.md
├─ proxy.py                 # local read-only proxy
├─ scraper.py               # full listings scraper (next step)
├─ output/
│  ├─ torrents.jsonl
│  └─ torrents.csv
└─ .env.example             # optional template, no secrets
```

## Run the proxy

```bash
.venv/bin/python proxy.py --host 127.0.0.1 --port 8787
```

Then browse via:

```text
http://127.0.0.1:8787/proxy?path=/
```

The proxy will be designed to:
- allow only `www.sharewood.tv`
- allow only safe read methods (`GET`, `HEAD`)
- forward `SHAREWOOD_COOKIE_HEADER`
- keep links navigable through proxy for DOM analysis

Available endpoints:
- `GET /` health/info JSON
- `GET /proxy?path=/some/path` proxied page or asset
- `GET /analyze?path=/some/path` quick HTML structure diagnostics

Example analysis call:

```bash
curl "http://127.0.0.1:8787/analyze?path=/"
```

## How scraper run will work (after proxy analysis)

The scraper is implemented in `scraper.py` and works directly against Sharewood (not through the proxy).

For Sharewood, the listing endpoint is typically `filterTorrents` with `_token`, `page`, and `qty=100`.
Default `--path-template` in the script is already configured for this endpoint.

Run direct mode (scraper sends cookies itself):

```bash
.venv/bin/python scraper.py \
  --start-page 1 \
  --end-page 1240 \
  --delay 1.5 \
  --resume \
  --out output/torrents.jsonl \
  --csv-out output/torrents.csv
```

Run proxy mode (recommended if you want cookies/tokens only in proxy process):

```bash
.venv/bin/python scraper.py \
  --proxy-base 'http://127.0.0.1:8787' \
  --discover-template \
  --start-page 1 \
  --end-page 1240 \
  --delay 1.5 \
  --resume \
  --out output/torrents.jsonl \
  --csv-out output/torrents.csv
```

In proxy mode, `scraper.py` does not read `SHAREWOOD_COOKIE_HEADER`; it asks the proxy for pages and auto-discovers the iframe listing URL template.
The `_token` is extracted from the proxied bootstrap page (`/torrents`) metadata if needed.
For reliability, discovery prefers `/torrents?page={page}` when it detects real page-to-page changes.

Current behavior:
- requests + BeautifulSoup parsing
- retry/backoff on transient failures
- incremental write after each page
- dedupe by torrent ID
- resumable execution by page range
- extracts `subcategory` from torrent icon path (e.g. `Films`, `Series`, `Comics`)
- stores both `uploaded` (estimated absolute ISO timestamp) and `uploaded_relative`

Useful options:
- `--keep-raw-columns` keeps full row text in JSONL for debugging selector quality
- `--retries` controls transient error retries (default `3`)
- `--base-url` overrides tracker base URL (default `https://www.sharewood.tv`)
- `--token` forces a specific `_token` value (otherwise auto-resolved from cookie/page)
- `--path-template` overrides endpoint format if Sharewood changes it
- `--proxy-base` fetches pages through local proxy instead of direct Sharewood requests
- `--discover-template` extracts iframe `filterTorrents` URL from bootstrap page via proxy
- `--bootstrap-path` page used for discovery/token extraction (default `/torrents`)

Recommended first run:

```bash
.venv/bin/python scraper.py \
  --start-page 1 \
  --end-page 3 \
  --delay 1.5 \
  --keep-raw-columns
```

Then inspect `output/torrents.jsonl` and confirm fields are correct before full run.

## Next action
- Validate authenticated access through `proxy.py`, inspect listing pages, then implement `scraper.py` with confirmed selectors.

## Static web viewer (GitHub Pages)

A static viewer is available in `viewer/`:
- `viewer/index.html`
- `viewer/app.js`
- `viewer/style.css`

Features:
- load local `CSV` or `JSONL` file
- search, category/subcategory filters, sorting, pagination
- shows `details_url` links and basic stats
- filter/sort/page state is mirrored in URL query params for refresh/share
- tries to cache the last loaded dataset in browser localStorage (small/medium files)

Local test:

```bash
python -m http.server 8080
```

Then open:

```text
http://127.0.0.1:8080/viewer/
```

GitHub Pages deployment:
1. Push this repo to GitHub.
2. In repository settings, enable GitHub Pages from `main` branch and root folder.
3. Open `https://<your-user>.github.io/<repo>/viewer/`.
4. Optionally commit an archive file under `viewer/data/` and open with `?data=./data/<file>`.
