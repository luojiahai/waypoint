# Waypoint — UI Design

**Date:** 2026-08-20
**Status:** Approved for planning
**Companion to:** `2026-08-19-waypoint-design.md`

## 1. Scope

This document specifies how Waypoint looks: palette, type, components, per-page layout, and the visual treatment of degraded and empty states. The system design — architecture, connectors, metrics, skills — lives in the companion spec and is not restated here.

Section references in the form §*n* point at the companion spec.

## 2. Design intent

Waypoint is read every morning by one person who already knows the team. It is an instrument panel, not a report: dense, monospaced, and unadorned, designed for a glance rather than a read. Three commitments follow from the companion spec and constrain everything below.

**Density serves the scan.** The Home page must answer "what needs attention?" without scrolling. Whitespace is spent on separating bands, not on breathing room inside them.

**Colour is semantic, never decorative.** Five colours carry meaning and nothing else uses them. A reader who learns that amber means "partial or medium severity" must never encounter amber used for emphasis.

**Layout enforces the people principle.** §4 forbids placing two people's numbers side by side. This is a layout rule before it is a policy, so the roster is cards and the person page states work mix in prose. Neither decision is stylistic; reverting either reintroduces the comparison the principle exists to prevent.

## 3. Visual language

### Palette

| Token | Hex | Use |
|---|---|---|
| `ground` | `#0f1216` | page background |
| `panel` | `#12151a` | panel and header background |
| `border` | `#232830` | panel borders, band separators |
| `border-dim` | `#1c2028` | row separators inside a panel |
| `text` | `#e6eaf0` | primary text and figures |
| `text-2` | `#8b93a1` | secondary text |
| `text-3` | `#6b7280` | labels, metadata, units |
| `text-4` | `#5a6270` | section labels, axis ticks |
| `ok` | `#7dd3a0` | complete, done, healthy, brand mark |
| `high` | `#e06c6c` | high severity, failed sync |
| `med` | `#e0b060` | medium severity, partial sync, scope added |
| `stale` | `#6ba3d6` | skill report predating the current sync |
| `skill` | `#a98fd6` | generated-content badge |

Fills for chart series are the muted variants `#3b6b4f` (ok), `#5a2f2f` (high), `#5c4520` (med), `#2a3038` (neutral remainder). Chart strokes use the full-strength tokens.

`ok` doubles as the brand mark and as "complete". These never collide in practice: the wordmark is the only green element in the header, and no metric appears there.

### Type

Monospace throughout — `ui-monospace, SFMono-Regular, Menlo, monospace`. A single family keeps figures aligned within a row and reinforces the instrument reading.

| Role | Size | Weight | Treatment |
|---|---|---|---|
| Page/entity title | 15px | 400 | — |
| Large figure | 17–19px | 400 | — |
| Body | 12.5px | 400 | line-height 1.45 |
| Explanatory prose | 13.5px | 400 | line-height 1.75, max 70ch |
| Section label | 10px | 400 | uppercase, letter-spacing .12em, `text-4` |
| Badge | 10px | 400 | uppercase, letter-spacing .08em |

Weight 600 is reserved for the wordmark. No other element uses it: emphasis comes from colour and size, which leaves weight available if a later need is genuinely stronger than both.

### Spacing and layout

An 8px base unit. Panels use 12px horizontal and 7px vertical internal padding; bands use 12–14px vertical and 16px horizontal. Panel gap is 12px. Border radius is 3px, applied to badges and buttons only — panels are square, which reads as instrumentation rather than as an application.

Maximum content width is unconstrained; the app is a local single-window tool and horizontal space is used. Prose blocks cap at 70ch regardless.

## 4. Components

**Panel.** 1px `border`, `panel` background, optional header strip separated by a 1px `border` rule. The header carries a section label on the left and a status badge or count on the right.

**Section label.** 10px uppercase in `text-4`. The only labelling mechanism; panels do not use sentence-case headings.

**Row.** Flex row, 7px vertical padding, 1px `border-dim` bottom rule, no rule on the last row. A row is title plus one metadata line plus a right-aligned age or date.

**Severity marker.** A fixed 40px column carrying `HIGH`, `MED`, or `LOW` in the matching colour. Text, not a dot — the word survives a screenshot and a colour-blind reader.

**Badge.** 10px uppercase, 1px border, 3px radius, background at roughly 12% of the border colour. Five exist and no more may be added without a matching entry in the palette table: `PARTIAL`, `FAILED`, `STALE`, `SKILL`, and a count badge such as `2 UNMATCHED`. Badge vocabulary matches the manifest's per-entity status values exactly (§8), so the word on screen is the word in `state/manifest.json`.

**Chip.** 1px `border` at 10.5px, 10px radius, `text-2`. Used for filters and inert deep links. A chip is never a write action.

**Button.** Primary is `ok` text on `#16241c` with a `#2f4a3a` border; only **Sync** and **Analyze** use it, and they are the only controls in the app that start work. Secondary is `text-2` on `#161a20` with a `#2a3038` border, used for the person-page window picker. Everything else that looks clickable is a chip or a link.

**Progress bar.** 5px, `border-dim` track, `#3b6b4f` fill. Used for epic completion only.

**Standing note.** `#161a20` background with a 2px `#3a424e` left rule, 11.5px `text-2`. Carries the people-principle label and the empty-state explanations.

### Charts

All charts are server-rendered inline SVG in the Jinja2 template layer, sized by `viewBox` and scaled to their container. There is no charting library (§12). Four chart types exist:

1. **WIP-limit bar** — a 16px track in `border-dim` with a fill sized to the column count against its limit, and the limit itself as a 1px `text-2` tick rising 2px past the track. Fill is `#3b6b4f` under limit and `#5c4520` at or over it; a column with no limit set uses a `text-3` fill, no tick, and the label `no limit`.
2. **Aging WIP** — one lane per WIP column against a shared horizontal age axis in days. Each in-flight item is a 4px dot: `#4a5260` under the threshold, `med` past it, `high` past twice it. The `issue_aging_days` threshold is a 1px dashed `#5c4520` vertical, annotated. Lane labels carry the column name and its count against limit. The oldest item is annotated with its key and age.
3. **Sparkline** — 1.6px polyline, `ok` for median and dashed `#4a7a5e` for p75, in a 200×42 box. Throughput uses `#3b6b4f` bars in the same box rather than a line, because a count per week is discrete.
4. **Progress bar** — 5px, `border-dim` track, `#3b6b4f` fill. Epic completion only.

No chart carries a goal line (§10). The only reference marks in the set are the WIP-limit tick and the aging threshold, both of which flag an item for attention rather than setting a number to reach.

Charts have no gridline labels beyond three y-ticks and four x-ticks, no legends where the series are annotated inline, and no tooltips. A figure that needs a tooltip belongs in a row.

**Arithmetic in a template is a defect** (§6). Charts receive pre-computed coordinate lists from `metrics/`; the template interpolates points into a `polyline` and does no scaling of its own.

## 5. Page layouts

### Chrome

A single header on every page: wordmark in `ok`, four nav items with the active one underlined 1px in `ok`, and on the right the sync state and the Sync button. The sync state reads `synced 09:12 · 2h ago` normally, and `last sync partial · 09:12` in `med` or `last sync failed · 09:12` in `high` otherwise.

### Home

Three bands, per the amended §12.

1. **Board strip** — one WIP-limit bar per board column across the full width, each labelled with the column name, its current count, and its limit. Trailing throughput sits on the right of the section label as `14 done in last 14d · 11 in the 14d before`. A line beneath names the oldest item in flight, its column, and its age. Columns come from `board_columns` in board order, so a column added in Jira appears without a code change.
2. **Risk register** — section label with the open count, then one row per threshold-crossing risk: severity marker, title, evidence line, right-aligned age. Ordered by severity then age. Rule-derived and skill-derived risks are interleaved; skill-derived rows carry the `SKILL` badge (§11).
3. **Queues** — three equal columns separated by 1px `border` rules: open PRs, issues in flight, blocked. Complete inventories, each truncated to four rows with a `+ n more` line in `text-3`.

### Delivery

One scrolling page. A sub-header carries jump chips (board / epics / flow) and a single window selector that applies to the flow section.

- **Board** — WIP-limit bars at 1 width beside the aging-WIP chart at 1.5 width. Beneath the chart, a row per item past the aging threshold: key, title, column, assignee, and age. An item can be old without being stalled, so this list and the stalled-issue risks overlap only partially, and each states which condition it met.
- **Epics** — one row per active epic: key and name at 150px, progress bar filling, completion count, and projection. Projection reads `~11 Sep · 9d past due` in `high` when drifting, `~3 Oct · on track` in `text-3` otherwise, and `no recent progress` when the trailing rate is zero (§10). The section label states the completion basis — by issue count or by story points.
- **Flow** — four equal panels: PR review latency, issue cycle time, WIP, weekly throughput. The first two show median and p75 as large figures over a sparkline; WIP shows current and median; throughput shows the trailing window against the preceding one over a bar sparkline. No reference lines (§10).

### People

Roster cards in a three-column grid, alphabetical, above a standing note carrying *signals to ask about, not measures of performance*. Each card: name, handle, then three lines of secondary text covering PRs and issues, review load, and workstreams with last activity. A card whose figures crossed a threshold takes a `med` border and renders the crossing figure in `med`.

Nothing on a card aligns with the same field on any other card. This is the point, and any future change that regularises card contents into aligned rows reintroduces the comparison §4 forbids.

### Person

Sub-header shows `people / Name`, the handles, and the window control: a date button defaulting to the last-opened timestamp from `state/person-views.json`, annotated with the resulting span. Then the standing note, then four panels in two rows of two — shipped, in flight, waiting on someone else, someone else waiting on them — then the work-mix panel, then the Analyze strip.

Work mix is a prose paragraph naming counts for both windows and stating whether volume changed, followed by two columns listing the issue keys behind each bucket. Buckets are coloured `ok` / `high` / `med` for feature / bug / toil; unmapped types appear as a fourth `other` bucket with its count whenever it is non-zero (§10).

### Sync

Two columns. Left: the last run as one row per source-entity with status, count, and — for anything not `ok` — a `med` or `high` explanatory line carrying the real error text and the resume watermark. Right: a rate-limit panel and a roster-health panel listing observed-but-unrostered identities, each with a suggested fix (`add to github.bot_logins`, or a note that activity is counted as `unattributed`).

## 6. Degraded states

§4 names silent incompleteness as the only failure mode that causes real harm. The treatment is therefore structural: **an affected panel is visually demoted, so the figure cannot be read without registering its status.**

A demoted panel takes a dashed border in the state colour, a tinted background, a section label in a muted variant of that colour, a plain-language reason line in place of the subtitle, and its figures at 50% opacity.

| State | Colour | Border/background | Reason line states |
|---|---|---|---|
| `PARTIAL` | `med` | `#5c4520` dashed on `#151209` | how much data arrived, why it stopped, that syncing again completes it |
| `FAILED` | `high` | `#5a2a2a` dashed on `#150c0c` | which source failed, the error, what is unaffected |
| `STALE` | `stale` | `#2a3a4a` dashed on `#0b1119` | when the report was generated and that data has changed since |

`STALE` is deliberately blue rather than amber. A skill report predating the current sync is not a data problem, and colouring it as one would train the reader to ignore amber.

**Partial panels render their figures.** A median over 62% of pull requests is directionally useful, and refusing to render would blank most of the Delivery page over one rate limit. The demotion carries the warning; the number stays available.

Degradation is per-panel and derives from the per-entity manifest status: a panel is demoted when any entity it reads is not `ok`. A Jira failure never demotes a PR panel.

## 7. Empty states

Every page has a real empty state (§15). Each is a standing note in the panel body — never a blank panel, and never a zero presented as if it were measured.

| Condition | Treatment |
|---|---|
| Fresh install, no sync yet | Every page shows a single centred panel: what Waypoint will show once synced, the Sync button, and a pointer to `waypoint doctor` if configuration is incomplete. |
| Board sets no WIP limits | Bars render as plain counts in `text-3` with `no limit set` beside each, and the WIP-limit risk rule is skipped rather than firing on a limit of zero. |
| Nothing in flight | Board strip states `Nothing in progress.` and the aging chart is replaced by the same line. An empty board is a legible result, not a rendering failure. |
| Risk register empty | `Nothing crossed a threshold.` in `ok`, with the count of items evaluated — so an empty register is legibly a result rather than a failure to load. |
| Queue empty | `No open pull requests.` in `text-3`. |
| Person inactive in window | Panels state `Nothing shipped in this window.` The page never renders zeroes, which would read as a score. |
| No reports generated | Skill panels show the Analyze button and one line naming what the skill produces. |
| Claude Code absent | Analyze strips are replaced by a note that generated analysis is unavailable, and the dashboard is otherwise unaffected (§15). |

## 8. Generated content

Skill output is always distinguishable from computed content. A skill-backed panel carries the `SKILL` badge in `skill` violet plus the skill name and generation time. Skill-derived rows merged into the rule-derived risk register carry the same badge inline.

The UI renders the JSON sidecar only, never the markdown (§13). An item with an empty `evidence` array is dropped before render. A report whose `inputs_digest` does not match the current manifest is demoted to `STALE`. A report with a malformed sidecar renders as a link to its markdown file, with a note, rather than disappearing.

## 9. Accessibility and offline

Severity, sync state, and work-mix bucket are each conveyed by a word as well as a colour, so no state depends on colour discrimination. Body text on `panel` exceeds 4.5:1; `text-4` is used only for labels above 10px where 3:1 applies. Dimmed figures in demoted panels drop below 4.5:1 by design — the reason line beside them carries the same information at full contrast.

The app is fully offline: vendored CSS, no webfonts, no charting library, no external requests. Fixed nav, no client-side routing, HTMX for the sync poll and the analyze poll only.

## 10. Open to revision

Not decided here, and safe to defer:

- Whether the Delivery window selector should also apply to the epics section.
- Whether the roster grid stays three columns at narrower window widths.
- Keyboard navigation. No shortcuts are specified; the app is mouse and link driven in v1.
