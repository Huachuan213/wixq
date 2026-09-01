# wixq

`wixq` is a focused reader for ZSXQ groups that the operator is authorised to
access. It reads text and image URLs through the group topics API, keeps an
immutable copy of every received page, and can resume safely after interruption.

It deliberately does not download PDF, MP3, video, or other attachments. It
does not read comments. Authentication material and collected content stay
local and are excluded from Git.

## Install

```powershell
cd wixq
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[browser,dev]"
```

## Login

Use a browser you control and sign in yourself. The command saves a local
session file; do not commit or share it.

```powershell
wixq login
```

If a regular Chrome instance is already running with remote debugging enabled,
the session can be saved without launching another browser:

```powershell
wixq login --cdp http://127.0.0.1:9222
```

## Read a time window

```powershell
wixq crawl "https://wx.zsxq.com/group/<group_id>" `
  --after "2026-08-31T15:00:00+08:00" `
  --before "2026-09-01T10:00:00+08:00" `
  --output "reports/zsxq-window"
```

The start time is inclusive and the end time is exclusive. The crawler uses a
page size of 20 and an `end_time` cursor by default.

## Local output

```text
reports/zsxq-window/
├── raw_pages/        # immutable request metadata + complete API responses
├── posts/            # derived per-topic JSON views
├── state.json         # resumable progress cache
├── failed_topics.json
├── topics.jsonl       # rebuilt derived view
└── markdown/          # rebuilt derived view
```

`raw_pages/` is the fact source. `state.json` is rebuilt from it when needed.
Use the following command to rebuild derived output at any time:

```powershell
wixq rebuild-output reports/zsxq-window
```

## Data handling

Only use Wixq on groups you are entitled to access. The project does not bypass
login, CAPTCHA, payment, or access controls. It never writes cookies or fetched
group content into the source repository.
