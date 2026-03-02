# La Cale Torrent Reup Tool

Static, pure client-side torrent manipulation tool.

## What it does

- Sets tracker to `https://tracker.la-cale.space/announce?passkey={passkey}`
- Removes creator and extra top-level metadata (keeps only `announce` and `info`)
- Sets `info.source` to `La Cale reup`
- Shows current vs changed values before download
- Shows torrent name, info hash before/after, and a collapsible file tree
- Names output as `[la-cale]content.title.torrent`

## Run locally

Just open `index.html` in a browser.

## Host on GitHub Pages

Put the `torrent-reup-web` folder in a repository and enable GitHub Pages on that folder (or repository root).
