# Waypoint

A locally-run, offline-capable dashboard for an engineering manager. It pulls GitHub
Enterprise and Jira Cloud data on manual demand into a `.waypoint/` directory, computes
delivery and people signals from it, and renders them as an instrument panel.

Waypoint runs on your machine, against your data, and reaches the network only when you
press Sync. There is no server to deploy and no account to create.

## What it is for

Waypoint answers the questions a manager asks on a Monday morning: what is close to
landing, what is stuck, where is work piling up, and what should I ask about in my
one-on-ones this week.

It is deliberately **not** a performance tool. That is enforced in the layout, not just
in the docs: no view anywhere places two people's numbers side by side, the roster is
cards rather than a table, work mix is prose rather than a chart, and every person page
carries the standing label *signals to ask about, not measures of performance*.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Read access to a GitHub Enterprise instance and a Jira Cloud site
- A **kanban** Jira board (Waypoint is kanban-only and never requests sprint data)
- Optionally, [Claude Code](https://claude.com/claude-code) — for the five analysis
  skills. Everything else works without it.

## Install

```bash
git clone <your-remote> waypoint
cd waypoint
uv sync
```

## Configure

Waypoint reads its configuration from `.waypoint/config.toml` in whichever directory you
run it from. Create it:

```toml
[github]
base_url = "https://ghe.corp.example.com"
repos = ["platform/api", "platform/web"]
bot_logins = ["dependabot", "renovate"]

[jira]
site = "example.atlassian.net"
project_key = "PROJ"
board_id = 42
story_points_field = "customfield_10016"

[sync]
backfill_days = 90

[thresholds]
pr_review_wait_days = 2
pr_approved_unmerged_days = 2
issue_stalled_days = 5
issue_aging_days = 10

[work_mix]
feature = ["Story", "Task"]
bug = ["Bug", "Defect"]
toil = ["Chore", "Support", "Maintenance"]

[[people]]
name = "Alex Rivera"
github_login = "arivera"
jira_account_id = "acct-alex"
active = true

[[people]]
name = "Bo Chen"
github_login = "bchen"
jira_account_id = "acct-bo"
active = true
```

Activity from an identity that matches no one in `[[people]]` is never dropped — it is
counted as *unattributed* and listed on the sync page so you can add the person or mark
the account as a bot.

### Credentials

Credentials are read **only** from the environment, never from `config.toml`:

| Variable | Purpose |
|---|---|
| `WAYPOINT_GITHUB_TOKEN` | GitHub Enterprise personal access token, read scopes only |
| `WAYPOINT_JIRA_EMAIL` | The email of the Jira account |
| `WAYPOINT_JIRA_TOKEN` | Jira Cloud API token |

A gitignored `.env` in the project root is loaded if present:

```
WAYPOINT_GITHUB_TOKEN=...
WAYPOINT_JIRA_EMAIL=you@example.com
WAYPOINT_JIRA_TOKEN=...
```

Waypoint is strictly read-only against both APIs. No token is ever written to a raw
file, the index, a report, a log, or a rendered page, and skill subprocesses run with
these variables stripped from their environment.

## Verify, sync, serve

```bash
uv run waypoint doctor   # check config, credentials, roster, reachability, board type
uv run waypoint sync     # fetch, then build the index
uv run waypoint serve    # open the dashboard on http://127.0.0.1:8787
```

`doctor` is the command to run when something looks wrong. It reports each check on its
own line and tells you what to do about any that fail — including the common case of a
scrum board, which Waypoint cannot use.

## The dashboard

Four pages:

- **Home** — the morning scan. A board strip with one bar per column against its WIP
  limit, the ranked risk register, and queues of open PRs, issues in flight, and blocked
  work.
- **Delivery** — the board in full, epic completion and projections, and flow metrics
  (review latency, cycle time, WIP, weekly throughput) over a window you choose.
- **People** — a roster card per person, and a per-person page of what they shipped,
  what is in flight, what they are waiting on, and what is waiting on them.
- **Sync** — per-entity status, watermarks, counts, rate-limit headroom, unattributed
  identities, and the Sync button.

Charts are server-rendered inline SVG. There is no charting library and no external
request of any kind — no CDN, no webfonts. The app works with the network off.

### Reading a degraded panel

If a sync fails or completes only partly, the panels that read the affected data are
visually demoted and badged `PARTIAL`, `FAILED`, or `STALE`, with a line explaining what
is missing and what to do. Waypoint would rather show you a marked-incomplete panel than
a confident wrong number.

No chart carries a goal line or a red/green threshold. The only two reference marks in
the whole application are the WIP limit tick and the issue-aging threshold, both of which
come from your own configuration.

## The skills

Five Claude Code skills read the same local data and write analytical reports back into
`.waypoint/reports/`:

| Skill | Produces |
|---|---|
| `waypoint:delivery-risk` | Ranked risk register with evidence and a suggested next move |
| `waypoint:delivery-review` | What is close to landing, what is aging, where flow is blocked |
| `waypoint:one-on-one-prep` | Per-person brief of what to ask about |
| `waypoint:workload-review` | Where load is uneven across the team |
| `waypoint:growth-review` | How a person's work mix has shifted over time |

Each reads data through `waypoint query` and must ground every claim in evidence drawn
from it — an item with no evidence is invalid and is dropped before rendering. Skill
output is validated before it reaches a page, and a report that predates the current
sync is badged `STALE`.

See [`docs/waypoint-skills.md`](docs/waypoint-skills.md) for installation. If Claude Code
is absent, the dashboard degrades cleanly to read-only and says so.

## CLI

| Command | Purpose |
|---|---|
| `waypoint serve` | Run the dashboard. `--port` (default 8787), `--no-open` |
| `waypoint sync` | Fetch from both sources, then rebuild the index |
| `waypoint build` | Rebuild the index from existing raw snapshots, without fetching |
| `waypoint doctor` | Check configuration, credentials, roster and connectivity |
| `waypoint query "<sql>"` | Read-only SQL against the index. `--format json\|table` |
| `waypoint capture-fixtures` | Capture redacted API payloads for local development |

All commands accept `--dir` to point at a project directory other than the current one.

`query` opens the index read-only and accepts a single `SELECT` — it exists so skills and
scripts have one honest way in, rather than each inventing its own.

## Data layout

```
.waypoint/
  config.toml     your configuration          (committable)
  reports/        skill output, markdown+json (committable)
  raw/            immutable JSONL snapshots   (gitignored)
  index.db        derived SQLite index        (gitignored)
  state/          manifest, progress, locks   (gitignored)
```

Raw snapshots are append-only and never mutated or deleted; each sync writes new
timestamped files. `index.db` is disposable — `waypoint build` reconstructs it from raw
at any time. `config.toml` and `reports/` stay out of `.gitignore` on purpose, so a team
can commit its configuration and its written reports.

## Development

```bash
uv run pytest
```

The suite runs fully offline: connector tests replay recorded fixtures through
`httpx.MockTransport`, and nothing spawns a real `claude` process.

The architecture is enforced by what each module may import:

- `sources/` knows HTTP and nothing about storage or presentation
- `store/` owns the filesystem and SQLite
- `metrics/` takes an open connection and plain values, and returns dataclasses — no I/O,
  and never reads the clock (anything time-dependent takes an explicit `now`, which is
  what makes it testable)
- `web/` renders and never computes — arithmetic in a template is a defect
- `skills_runner.py` is the only module that knows the Claude CLI exists

Design rationale lives in [`docs/superpowers/specs/`](docs/superpowers/specs/): the system
design covers architecture, connectors, metrics and risk rules; the UI design covers the
palette, components and page layouts.
