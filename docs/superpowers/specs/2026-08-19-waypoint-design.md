# Waypoint — Design

**Date:** 2026-08-19
**Status:** Approved for planning

## 1. Overview

Waypoint is a locally-run web dashboard for an engineering manager. It pulls data from GitHub Enterprise and Jira Cloud on manual demand, stores everything under `.waypoint/` in the working directory, and presents two things: the state of project delivery, and the state of the people doing it.

It ships with Claude skills that read the same stored data and write analytical reports back into `.waypoint/`. The dashboard renders those reports alongside its own computed panels.

The dashboard is the product. Every panel must be useful with no LLM involvement; skills enrich what is already there.

## 2. Goals

- A single page an EM can scan each morning to know what needs attention.
- Per-person pages that make a 1:1 better prepared without measuring the person.
- Delivery visibility: board health, epic progress, flow trends, and a derived risk register.
- Data on local disk, in a form both the app and an LLM can read.
- New data sources can be added without changing anything downstream of the connector.

## 3. Non-goals

- No hosting, no multi-user, no authentication. Localhost only, single operator.
- No automatic or scheduled sync. The user presses a button.
- No writes back to GitHub or Jira. Waypoint is strictly read-only against its sources.
- No individual performance measurement, scoring, or ranking. See Principles.
- Not in v1: change-detection digests, meeting-ready exports, local notes and follow-ups, collaboration/bus-factor mapping.

## 4. Principles

**Waypoint does not score individuals.** Per-person views answer "what should I ask about?" and never "how is this person performing?" This is enforced structurally, not by convention:

- No leaderboards, no per-person velocity or output totals, no ranking, no sortable productivity column.
- No view anywhere places two people's numbers side by side. This rules out tabular layouts of per-person figures entirely, sortable or not: a column is a ranking whether or not it can be reordered.
- Person pages carry a standing label: *signals to ask about, not measures of performance*.
- The two people-facing skills are instructed to emit questions, never assessments, and are forbidden from comparing people.

**Never render silently-incomplete data.** A panel built on stale, partial, or failed input is badged as such. Showing a chart that quietly omits half its data is the only failure mode here that causes real harm, because the user would act on it.

**Raw data is immutable and re-derivable.** Metric definitions will change with use. Raw API payloads are never mutated, so definitions can be revised and the index rebuilt without re-fetching.

## 5. Context and scale

- One team, direct reports: roughly 5–10 ICs.
- 1–5 GitHub repositories on GitHub Enterprise (self-hosted, custom base URL).
- One Jira Cloud project and one **kanban** board.

**Waypoint is kanban-only, and this is a scoping decision rather than a limitation to work around.** The team runs a kanban board, so Waypoint has no concept of a sprint: no commitment, no burndown, no scope-creep measurement. Where a scrum tool would ask "will the committed work land by Friday?", Waypoint asks the kanban equivalent — is too much started, and is anything getting old. Supporting both cadences would double the delivery surface for a mode nobody here uses.

`waypoint doctor` reads `GET /rest/agile/1.0/board/{id}` and fails with a clear message if the configured board's `type` is not `kanban`, rather than letting the connector 400 on a sprint request mid-sync. Adding scrum support later means a second cadence module and a branch in the risk rules; nothing else in the architecture assumes either mode.
- First sync backfills 90 days. Subsequent syncs are incremental from a per-entity watermark.
- Expected data volume: low thousands of records. SQLite is comfortably sufficient.

## 6. Architecture

Three stages, each independently runnable from the CLI:

```
fetch ──▶ .waypoint/raw/*.jsonl ──▶ build ──▶ .waypoint/index.db ──▶ serve
```

```
waypoint/
  cli.py              # waypoint serve | sync | build | doctor | query | capture-fixtures
  config.py           # .waypoint/config.toml + secrets from environment
  roster.py           # people ↔ github login ↔ jira accountId
  sources/
    base.py           # Source protocol: fetch(since) -> Iterator[RawRecord]
    github.py         # GitHub Enterprise connector
    jira.py           # Jira Cloud connector
  store/
    raw.py            # append-only JSONL writer/reader, sync manifest
    index.py          # SQLite schema, build-from-raw
    reports.py        # read/write .waypoint/reports/
  metrics/
    flow.py board.py epics.py risks.py people.py
  web/
    app.py routes/ templates/ static/
  skills_runner.py    # the only module that knows Claude CLI exists
skills/               # shipped Claude skills, installed to .claude/skills/
tests/
  fixtures/           # recorded, redacted API responses
```

### Module boundaries

- **`sources/`** knows how to talk to APIs and nothing else. Each connector emits raw payloads; no metric logic, no interpretation. Adding a source later means one new file implementing the `Source` protocol, with no downstream change.
- **`store/raw.py`** is append-only and never interprets payloads.
- **`store/index.py`** is the only module that knows the normalized schema.
- **`metrics/`** is pure functions: a database handle in, dataclasses out. No HTTP, no filesystem access, no framework imports. Independently testable against fixtures.
- **`web/`** renders and never computes. Arithmetic in a template is a defect: it means a metric escaped its module.
- **`skills_runner.py`** quarantines everything about subprocessing the Claude CLI, so the dashboard degrades cleanly to read-only when Claude Code is absent.

### Sync control flow

`POST /sync` starts a background task that runs fetch then build. HTMX polls a status endpoint for progress. One sync at a time, guarded by `.waypoint/state/sync.lock`. No websockets, no job queue.

## 7. Data layout

```
.waypoint/
  config.toml              # sources, repos, jira project, roster — no secrets
  .gitignore               # ignores raw/, index.db, state/
  raw/
    github/pull_requests/2026-08-19T09-12-03Z.jsonl
    github/reviews/…
    jira/issues/…
    jira/changelogs/…
    jira/board_config/…
  index.db                 # derived, disposable
  reports/
    2026-08-19-delivery-risk.md
    2026-08-19-delivery-risk.json
  state/
    manifest.json          # per-entity watermarks, status, counts, digest
    sync.lock
    person-views.json      # last-viewed timestamp per person (view preference)
```

`.waypoint/.gitignore` ignores `raw/`, `index.db`, and `state/`, leaving `config.toml` and `reports/` committable if the user wants report history in git.

### Raw record envelope

Every line in every raw JSONL file:

```json
{"source": "github", "entity": "pull_request", "id": "repo#482",
 "fetched_at": "2026-08-19T09:12:03Z", "payload": { }}
```

`payload` is the untouched API response. Each sync writes a new timestamped file per entity rather than appending to an existing one, so every sync is a point-in-time snapshot. Build resolves duplicates by last-writer-wins on `fetched_at` per `(source, entity, id)`. Nothing is ever mutated or deleted.

### Secrets

Secrets never touch `.waypoint/`. Read from the environment:

- `WAYPOINT_GITHUB_TOKEN`
- `WAYPOINT_JIRA_EMAIL`
- `WAYPOINT_JIRA_TOKEN`

A gitignored `.env` in the project root is loaded if present. Config holds URLs and IDs only. Tokens are never written to raw files, the index, reports, or logs.

### Config format

```toml
[github]
base_url = "https://ghe.corp.example.com"
repos = ["platform/api", "platform/web"]
bot_logins = ["dependabot", "renovate"]

[jira]
site = "example.atlassian.net"
project_key = "PROJ"
board_id = 42                             # must be a kanban board; doctor verifies
story_points_field = "customfield_10016"  # empty string disables point-based metrics

[sync]
backfill_days = 90

[thresholds]
pr_review_wait_days = 2
pr_approved_unmerged_days = 2
issue_stalled_days = 5
issue_aging_days = 10       # total time in flight, regardless of movement

[work_mix]
feature = ["Story", "Task"]
bug = ["Bug", "Defect"]
toil = ["Chore", "Support", "Maintenance"]

[[people]]
name = "Alex Rivera"
github_login = "arivera"
jira_account_id = "5b10a2844c20165700ede21g"
active = true
```

## 8. Connectors

### GitHub Enterprise

Base URL is configurable; GraphQL at `{base_url}/api/graphql`, REST at `{base_url}/api/v3`. Auth is a PAT bearer token.

GraphQL is the primary path. This is deliberate: review latency requires each PR's reviews and review requests, which over REST is an N+1 request per PR. One GraphQL query per repository page retrieves pull requests together with reviews, review requests, timeline events, and first-commit date. `waypoint doctor` probes GraphQL availability and the connector falls back to REST if the GHE version does not support the required fields.

Entities fetched: pull requests (author, created/ready/merged/closed timestamps, draft state, additions/deletions, changed files, labels, base ref, first commit date), reviews (reviewer, state, submitted_at), and review requests.

Incremental fetch filters on PR `updatedAt` greater than or equal to the watermark.

### Jira Cloud

`https://{site}/rest/api/3` and the Agile API at `/rest/agile/1.0`. Auth is HTTP Basic with email plus API token.

Issues are fetched via JQL filtered on `project = {key} AND updated >= "{watermark}"`, **with `expand=changelog`**. The changelog is load-bearing: status transitions are the sole source of cycle time, stall detection, item age, and historical WIP. Without it none of the flow metrics can be computed at all, since the issue resource carries only current status.

Entities fetched: issues (key, type, status, status category, assignee, reporter, labels, parent/epic link, story points if configured, created/updated/resolved), issue changelogs, and the board configuration.

Board configuration comes from `GET /rest/agile/1.0/board/{id}/configuration`, which returns the board's columns in order, the statuses mapped into each, and each column's WIP limit where one is set. This is fetched every sync — it is a single small request, and a column added in Jira must not silently vanish from the dashboard. Sprints are never requested; the board is kanban and `/board/{id}/sprint` would return 400.

### Shared connector behaviour

- Both honour `Retry-After` and apply exponential backoff. GitHub secondary rate limits are expected during the first 90-day backfill.
- The manifest is checkpointed after each page, so an interrupted backfill resumes rather than restarting.
- Per-entity status is recorded as `ok`, `partial`, or `failed` with the error message.

## 9. Index schema

Normalized tables built from raw:

- `people` — id, name, github_login, jira_account_id, active
- `repos` — id, name, url
- `pull_requests` — id, repo_id, number, author_person_id, title, state, draft, created_at, ready_at, merged_at, closed_at, first_commit_at, additions, deletions, changed_files, url
- `pr_reviews` — id, pr_id, reviewer_person_id, state, submitted_at
- `pr_review_requests` — pr_id, requested_person_id, requested_at
- `jira_issues` — key, type, status, status_category, assignee_person_id, parent_key, labels, story_points, created_at, resolved_at, url
- `issue_transitions` — issue_key, field, from_value, to_value, changed_at, author_person_id
- `board_columns` — id, name, position, wip_limit (nullable), status_ids
- `sync_runs` — id, started_at, finished_at, status, per-source counts, manifest digest

Two derived tables precompute per-item durations so metric functions stay simple selects:

- `pr_flow` — pr_id, time_to_first_review, time_to_merge, time_in_review, review_wait_current
- `issue_flow` — issue_key, first_in_progress_at, first_done_at, cycle_time, last_transition_at, days_since_transition

Person attribution is resolved at build time from the roster. Activity from a GitHub login or Jira account not present in the roster is attributed to an explicit `unattributed` bucket, never dropped.

## 10. Metric definitions

These are stated precisely because ambiguity here produces silently wrong numbers.

- **PR review latency** — from PR ready-for-review (or `created_at` when the PR was never a draft) to the first review submitted by someone other than the author. Bot accounts listed in config are excluded.
- **PR cycle time** — from first commit authored date to `merged_at`. Unmerged PRs have no cycle time.
- **PR review wait (open PRs)** — now minus ready-for-review, for open PRs with no submitted review.
- **Issue ↔ PR link** — a pull request is linked to an issue when the issue key matches `{project_key}-\d+` in the PR title, head branch name, or body. This is the only linking mechanism used; Jira's development-panel integration is not queried, since it requires an app connection that may not exist.
- **Issue cycle time** — from the first transition into a status whose category is *In Progress*, to the first transition into a status whose category is *Done*. Status *categories* are used, not status names, so workflow renames do not break it.
- **Issue stalled** — issue is in an In Progress category status and has had no transition, and no linked PR activity, for `thresholds.issue_stalled_days` calendar days.
- **WIP** — count of issues in an In Progress category status at a given point in time, reconstructed from `issue_transitions`.
- **Throughput** — count of issues reaching a Done-category status within a window. Reported for a trailing window alongside the immediately preceding window of equal length. This is a team-level count and never attributed per person.
- **Column WIP** — count of issues whose current status maps into a given board column, from `board_columns.status_ids`. A column is *over limit* when its count exceeds `wip_limit`. Columns with no limit set are never over limit, and the panel says so rather than showing a limit of zero.
- **Item age** — now minus the first transition into an In Progress category status, for issues not yet Done. Distinct from *issue stalled*: an item can move between columns daily and still be old.
- **Epic completion** — done child issues divided by total child issues, by count. If `jira.story_points_field` is configured and populated on more than 80% of children, points are used instead and the panel states which basis is in use.
- **Epic projected finish** — remaining children divided by the completion rate over the trailing 4 weeks, projected forward from today. Undefined when the trailing rate is zero, in which case the panel shows "no recent progress" rather than a date.
- **Epic projection drift** — projected finish minus due date. Only shown when the epic has a due date.
- **Workstreams touched (per person, per window)** — count of distinct epics plus distinct repositories with activity attributed to that person.
- **Work mix (per person, per window)** — issues resolved, bucketed into feature / bug / toil via the `[work_mix]` issue-type mapping. Unmapped types are bucketed as *other* and the count is shown.

All flow metrics are reported as median and p75 over rolling windows. **Flow metrics carry no target lines and no red/green thresholds** — a cycle time with a goal line becomes a number to game.

This rule has **no exceptions**. Because the board is kanban there is no commitment to burn down against, so no chart in Waypoint carries a goal line. The only reference marks anywhere are configured risk thresholds — the aging marker on the WIP chart and each column's WIP limit — which flag an item for attention rather than setting a number to hit.

## 11. Risk rules

The risk register is rule-derived and always present, independent of any skill. Skill-generated risks are merged into the same view and badged by origin.

| Rule | Severity basis |
|---|---|
| PR open without review beyond `pr_review_wait_days` | escalates with age |
| PR approved but unmerged beyond `pr_approved_unmerged_days` | escalates with age |
| Issue in progress with no transition beyond `issue_stalled_days` | escalates with age |
| Issue flagged or in a blocked status | high |
| Issue in a WIP column with no assignee | medium |
| Epic projected to finish past its due date | escalates with drift |
| Column WIP above its configured limit | medium; skipped entirely when the column sets no limit |
| Issue in flight beyond `issue_aging_days` | escalates with age |
| All in-flight children of an epic assigned to one person | medium |
| Issue reopened more than once | medium |

Each risk carries what, why, and a link to the underlying item.

## 12. Dashboard

Four pages, Jinja2 templates with HTMX. CSS is vendored so the app works fully offline. There is no charting library: every chart in Waypoint — WIP-limit bars, the aging-WIP chart, sparklines, proportion bars — is server-rendered inline SVG, which is less code than configuring a library and removes an offline-asset dependency. Visual design, palette, component inventory, and per-page layout are specified separately in `2026-08-20-waypoint-ui-design.md`.

### Home

The morning scan, in three bands. Header holds the last-sync timestamp and the Sync button.

1. A board strip: one bar per board column, filled against that column's WIP limit with the limit drawn as a tick, plus a line naming the oldest item in flight and its age. Trailing throughput sits to the right as a single figure against the preceding window. A column over its limit is the loudest element in the band.
2. The risk register, ranked. This band carries **only items that crossed a threshold** in §11.
3. Three queue columns: open PRs, issues in flight, and blocked items. These are **complete inventories**, not a second cut of the register — every open PR appears here, including the fourteen that are perfectly healthy.

The register and the queues answer different questions — "what is wrong?" and "what is outstanding?" — so an item appearing in both is correct rather than duplicated. Every row links through to the underlying item in GitHub or Jira. Waypoint renders no action controls of its own, per §3.

### Delivery

- **Board** — WIP by column against limits, and the aging-work chart: one dot per in-flight item, positioned by age along a shared axis and laned by column, with the `issue_aging_days` threshold marked. This is the section that answers "what is quietly going stale", which on a board with no deadline is the only thing that degrades on its own.
- **Epics** — completion, activity, and projection drift.
- **Flow** — cycle time, review latency, WIP, and weekly throughput over rolling windows. Cycle time and review latency are reported as median and p75; WIP and throughput are counts. Distributions and trends only.

### People

The roster is a grid of **cards, one per person** — deliberately not a table. Each card states the same facts: open PRs, PRs awaiting their review, in-flight issues, workstreams touched, and days since last activity. These describe load and shape, never output.

A table would satisfy the letter of the rules below — no totals column, no productivity sort — while breaking the principle in §4, because column alignment *is* side-by-side comparison. Scanning a column of "issues in flight" ranks people whether or not the column is sortable. Cards carry identical information with no column to scan, so the reader takes each person on their own terms. Cards are ordered alphabetically. A card is outlined in amber when something on it crossed a threshold.

The person page is the 1:1 page. A "since" window, then: shipped in that window, in flight with ages, what they are waiting on and who is waiting on them, and work mix for the recent window against the prior window of equal length.

The work-mix comparison is the growth view: it shows whether someone has been on maintenance for two months without producing a score. **It is rendered as prose with the issue keys named under each bucket, not as a chart.** A chart of two windows makes the direction of change the loudest thing on the page, and a descending line reads as decline regardless of the label above it. Naming the issues gives the reader something to open a conversation with — "you spent this fortnight on PROJ-97, PROJ-113 and PROJ-124" — rather than a trend to react to. Volume is stated for both windows so a change in composition is not mistaken for a change in output.

The "since" window defaults to the last time that page was opened, stored per person in `state/person-views.json`. This is a view preference, and the window is adjustable with a date picker.

The page carries the standing label *signals to ask about, not measures of performance*.

### Sync

Last run per source, record counts, real error messages, rate-limit state, and roster health — including any GitHub logins or Jira accounts observed in the data that are absent from the roster.

### Skill invocation from the dashboard

Panels backed by a skill carry an **Analyze** button. `POST /analyze/{skill}` invokes `skills_runner`, HTMX polls for progress, and on completion the report lands in `.waypoint/reports/` and the panel re-renders with the narrative, badged with the skill name and generation time so computed content is always distinguishable from generated content.

## 13. Skills

Five skills, installed to `.claude/skills/` so they are invocable as `/waypoint:<name>` in a Claude Code session in this directory, and invoked headless by `skills_runner` for the dashboard buttons. One definition, two entry points.

| Skill | Produces |
|---|---|
| `waypoint:delivery-risk` | Ranked risk register with evidence and a suggested next move |
| `waypoint:delivery-review` | What is close to landing, what is aging, and where flow is blocked |
| `waypoint:one-on-one-prep` | Per-person brief of what to ask about |
| `waypoint:workload-review` | Where load is uneven across the team |
| `waypoint:growth-review` | How a person's work mix has shifted over time |

### Output contract

Every skill writes two files to `.waypoint/reports/`: a markdown report for the user and for git, and a JSON sidecar the dashboard renders. **The UI never parses prose** — if it did, a rephrased sentence would break a panel.

Sidecar schema:

```json
{
  "skill": "waypoint:delivery-risk",
  "generated_at": "2026-08-19T09:40:00Z",
  "window": {"from": "2026-08-05", "to": "2026-08-19"},
  "inputs_digest": "sha256:…",
  "items": [
    {
      "severity": "high",
      "title": "Checkout rework has no reviewer coverage",
      "body": "…",
      "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://…"}],
      "question": "Who can back up Alex on the checkout rework this week?"
    }
  ]
}
```

`inputs_digest` is a hash of the sync manifest. The dashboard compares it against the current manifest to badge a report as stale when data has synced since generation.

### Grounding rule

Every skill's instructions require that each claim cite a specific pull request or issue, and that anything unsupported by the data is written as "insufficient data" rather than inferred. This is the dominant failure mode for LLM analysis over metrics: plausible narrative that is not in the numbers. The schema enforces it structurally — an item with an empty `evidence` array is invalid and is dropped on render.

### Data access

Skills read the index through the shipped `waypoint query "<sql>"` command rather than hand-rolling sqlite invocations, and may drop to raw JSONL when they need full payloads. PR descriptions and issue comments live only in raw, and they are usually where the signal about *why* something is stuck actually is.

### People-skill constraints

`one-on-one-prep` and `growth-review` inherit the principle explicitly in their instructions: emit questions, never assessments; never compare people; never characterize performance. `growth-review` describes what changed in the work and leaves interpretation to the reader.

## 14. CLI

- `waypoint serve` — start the local web app (default port 8787), open browser.
- `waypoint sync` — fetch then build. Same path the button uses.
- `waypoint build` — rebuild the index from existing raw data.
- `waypoint doctor` — validate config, tokens, connectivity, GraphQL availability, roster completeness, and that the configured board is kanban.
- `waypoint query "<sql>"` — read-only query against the index, used by skills.
- `waypoint capture-fixtures` — record and redact API responses into `tests/fixtures/` (development only).

## 15. Error handling

Partial failure is the normal case for a daily two-API sync, not an exception.

- Per-entity status in the manifest. A Jira failure never discards what GitHub returned.
- Any panel built on stale, partial, or failed input is badged. No chart silently omits data.
- `waypoint doctor` validates everything before the first sync, so the common first-run failure is a clear message rather than a stack trace mid-backfill.
- 401, 403-scope, and 403-rate-limit produce distinct, actionable messages. Rate-limit waits report the wait duration rather than appearing hung.
- Build is idempotent and writes to a temporary database swapped in atomically. A failed build leaves the working index untouched.
- Unattributed activity is surfaced on the Sync page, never dropped.
- If Claude Code is absent or a skill run fails or times out, the dashboard degrades to read-only with a plain message. A report with a malformed sidecar is retained and linked as markdown rather than discarded.
- Every page has a real empty state: fresh install, no sync yet, a board whose columns set no WIP limits, nothing in flight.

## 16. Testing

In priority order.

**`metrics/`** gets the most coverage. Pure functions, table-driven tests against fixture databases: given this transition sequence, cycle time is exactly this value. This is where the bugs will be and where they are invisible.

**Connectors** are tested against recorded fixtures — real API responses captured once, redacted (tokens stripped, names synthesized), replayed offline. No test touches the network.

**Build** gets an idempotency test (building twice from identical raw yields identical content) and a last-writer-wins test across overlapping snapshots.

**Web routes** get smoke tests in three states — full data, empty data, and partial-sync — because degraded states are never exercised by hand and all of them occur on day one.

**Skills** cannot have their prose tested, but the sidecar schema can. A shipped validator, plus a test that each skill's example output passes it, catches the realistic failure of a skill drifting its output format.

Development follows TDD.

## 17. Future work

Deferred deliberately, and the design accommodates each without restructuring:

- Change-detection digest ("what changed since you last looked"). The point-in-time snapshot layout already supports it.
- Meeting-ready markdown and HTML exports.
- Local notes and follow-ups attached to people and issues.
- Collaboration and bus-factor mapping.
- Additional sources (Linear, PagerDuty, CI) via the `Source` protocol.
- Scrum boards: sprints, commitment, burndown, and scope-creep detection. Cut from v1 because the team runs kanban (§5). Adding it means a `metrics/sprint.py` beside `board.py`, a sprint fetch in the Jira connector, two extra tables, two extra risk rules, and a branch on board type in the Home band and Delivery's first section. Nothing else assumes a cadence, so this stays additive.
- Cycle-time percentile as the aging threshold, replacing the fixed `issue_aging_days`. More honest than a flat day count across work of different sizes, but it needs enough history to be stable and a fixed threshold is legible on day one.
