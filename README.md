# Telegram Crawler

A web app that downloads media (photos, videos, audio) from your Telegram channels and groups,
and lets you browse and manage what you downloaded.

## Features

Open the home page. Each card is one page:

- **Download** — pick channels, media type, batch size and message limit. Live progress and log.
  You can add channels you are not a member of (`@name`, `https://t.me/...`, or `-100...` id).
- **Viewer** — browse media by channel. Paging, filter by type, search, sort. Video thumbnails (ffmpeg).
- **Duplicates** — finds exact copies (same SHA-256) inside a group. Keeps the oldest, deletes the copies you tick.
- **Discover** — find new groups by keyword and join them. Safe limits: max joins per day and a wait between joins.
- **Files Manager** — browse the downloads folder. See folder sizes and free disk space.
  Delete files or whole folders. Deleted files are remembered, so they are **not** downloaded again.
- **Login** — optional HTTP Basic Auth for the whole site (`VIEWER_USER` + `VIEWER_PASS`).

Delete actions are blocked while a download is running.

## Settings

All settings live in `.env`. Copy `.env.example` to `.env` and fill it in. The main keys:

| Key | What it does |
| --- | --- |
| `API_ID`, `API_HASH` | Required. Get them at https://my.telegram.org/apps |
| `DOWNLOADS_DIR` | Where media is saved |
| `DATA_DIR` | Where the app keeps its own files |
| `VIEWER_USER`, `VIEWER_PASS` | Turn on the login. Leave empty to turn it off |
| `BATCH_SIZE`, `MAX_MESSAGES` | Defaults for the Download page |
| `JOIN_DAILY_LIMIT`, `JOIN_MIN_INTERVAL` | Safety limits for the Discover page |

See `.env.example` for the full list.

## Where data lives

- **Downloads folder** — one sub-folder per channel or group.
- **Data folder** — the app's own files:
  - `<SESSION_NAME>.session` — Telegram login
  - `download_state.json` — what was downloaded (and what you deleted)
  - `file_hashes.json` — file fingerprints for the Duplicates page
  - `channels.txt` — channels you added on the Download page
  - `join_log.json`, `invites.json` — Discover page history
  - `.thumb_cache/` — video thumbnails

Back up the data folder. Without it you must log in again and old media may be downloaded again.

## How we run it today

Home server: openSUSE MicroOS, rootless **podman**.

| What | Path |
| --- | --- |
| Project | `/srv/disk2/telegram_crawler` |
| Media | `/srv/disk2/telegram_downloads` |
| App data | `/srv/disk2/telegram_data` |

### Build

```sh
cd /srv/disk2/telegram_crawler && podman build -t telegram-crawler .
```

### Log in to Telegram (once)

```sh
podman run --rm -it --userns=keep-id:uid=1000,gid=1000 --env-file .env -e DATA_DIR=/data -v /srv/disk2/telegram_data:/data:Z telegram-crawler python main.py --login
```

Enter your phone number and the code Telegram sends you.

### Run

```sh
podman run -d --name telegram-crawler --userns=keep-id:uid=1000,gid=1000 --env-file .env -e DOWNLOADS_DIR=/downloads -e DATA_DIR=/data -e VIEWER_HOST=0.0.0.0 -e VIEWER_PORT=5000 -v /srv/disk2/telegram_downloads:/downloads:Z -v /srv/disk2/telegram_data:/data:Z -p 5000:5000 telegram-crawler
```

Then open `http://<server-ip>:5000`. Turn on the login (`VIEWER_USER` + `VIEWER_PASS`) — the page is open to your network.

### Why these flags

- `--userns=keep-id:uid=1000,gid=1000` — the app runs as uid 1000 in the container. Without this it cannot write to `/data`.
- `:Z` on volumes — SELinux label, so the container may read and write the folders.
- `-e DOWNLOADS_DIR=... -e DATA_DIR=...` — `.env` holds host paths. These overrides set the paths inside the container.

### Update to a new version

```sh
cd /srv/disk2/telegram_crawler && git pull && podman build -t telegram-crawler .
```

```sh
podman stop telegram-crawler && podman rm telegram-crawler
```

Then start it again with the **Run** command. Media and data are safe: they live in the mounted folders, not in the container.

### Useful commands

```sh
podman logs -f telegram-crawler
podman restart telegram-crawler
```

## Other ways to run

### Local (Python)

Needs Python 3.12 and ffmpeg (for video thumbnails).

```sh
pip install -r requirements.txt
cp .env.example .env   # fill in API_ID / API_HASH
python main.py --login # once
python main.py         # open http://127.0.0.1:5000
```

### Docker Compose

```sh
cp .env.example .env
mkdir -p <DOWNLOADS_DIR> <DATA_DIR>
docker compose build
docker compose run --rm crawler python main.py --login
docker compose up -d
```
