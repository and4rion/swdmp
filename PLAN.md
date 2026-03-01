# Sharewood Listing Archive Plan

## Goal
Create a safe, local, cookie-backed proxy for `https://www.sharewood.tv` so authenticated pages can be inspected, then build a robust Python scraper to export all torrent listings (1240 pages, ~100 torrents/page).

## Scope and order
1. Build proxy for authenticated browsing and HTML structure analysis.
2. Identify stable selectors and pagination mechanics from real pages.
3. Implement scraper using `requests` + `BeautifulSoup`.
4. Run full export with resume/retry and incremental persistence.

## Phase 1 - Local proxy (read-only)
- Bind only to `127.0.0.1` (never public).
- Target host locked to `www.sharewood.tv`.
- Accept only `GET`/`HEAD` methods.
- Read raw cookie header from env var: `SHAREWOOD_COOKIE_HEADER`.
- Forward browser-like headers (`User-Agent`, `Accept`, `Accept-Language`) and pass upstream responses through.
- Rewrite HTML links (`href`, `src`) to remain navigable via proxy.
- Basic diagnostics:
  - upstream status and final URL
  - login redirect detection
  - auth failure hints (401/403)

Deliverable: local proxy script and verified access to listing pages through browser.

## Phase 2 - Structure analysis
- Inspect listing page DOM and pagination.
- Confirm stable selectors for:
  - torrent unique ID
  - title
  - details URL
  - category
  - size
  - seeders / leechers
  - upload date/time
- Validate selectors on multiple pages to avoid brittle parsing.

Deliverable: documented selector map and sample parsed rows.

## Phase 3 - Scraper implementation
- Use `requests.Session` + `BeautifulSoup` (`lxml` parser).
- Iterate pages (default `1..1240`) with options:
  - `--start-page`
  - `--end-page`
  - `--delay` (polite throttling)
- Add retry/backoff for network errors and `429/5xx`.
- Parse and normalize all fields.
- Persist incrementally each page (JSONL first, CSV optional).
- Deduplicate by torrent ID.

Deliverable: CLI scraper producing resumable export files.

## Phase 4 - Validation and runbook
- Dry-run on small page range and inspect output quality.
- Full run with checkpointing and progress logging.
- Post-run checks:
  - expected row count
  - duplicate count
  - parse failures report

Deliverable: reproducible run instructions and quality checks in README.

## Safety and etiquette
- Do not perform write actions on tracker; listing reads only.
- Keep a modest request rate (e.g., 1-2s delay).
- Never commit or print cookie values in logs.
- Keep proxy local-only and domain-restricted.
