# Implementation decisions

Waypoint was built from `docs/superpowers/plans/2026-08-20-waypoint.md` — a 30-task
plan argued from the two specs in `docs/superpowers/specs/`. Each task was implemented
and independently reviewed, and a whole-branch review ran at the end.

Along the way the plan turned out to contain defects: assertions that could never pass,
assertions that could never fail, code that violated constraints the plan itself set,
and several places where two parts of the plan could not both be true. Each was decided
rather than guessed at, and every decision is recorded below with its reasoning and what
it costs if it was wrong.

**The specs are the binding authority.** Where the plan and a spec disagreed, the spec
won. Where the plan disagreed with itself, the decision is recorded here.

This file exists so that any of these can be found and undone. If something in the code
looks arbitrary, it is probably here with a reason.

## The decisions that most affect the code

If you read nothing else, read these.

- **`Person.id` is derived from the display name** (#43, parked at #44). Renaming
  someone changes their id and their URL, and orphans their stored reports. Kept because
  human-readable person slugs are the evident intent and 20+ downstream fixtures depend
  on them.
- **Childless epics are excluded from the epics section** (#61, parked at #62).
  "Activity is never dropped" protects activity records; an epic with no children has no
  activity to drop, and listing it would render a meaningless `0 / 0`.
- **The REST connector reports `requested_at` as `"unknown"`** (#50) rather than
  substituting the pull request's creation time. The plan fabricated a plausible-looking
  timestamp that would have produced a confidently wrong review-wait metric.
- **The tenth risk rule was missing** (#36). The plan's own interface promised all ten
  rules from spec §11 and implemented nine; `epic_drift` was added.
- **`waypoint doctor` was missing four of its nine checks** (#38) — GitHub reachability,
  repo readability, Jira reachability, and board-configuration readability.
- **Jira changelog truncation is now detected and reported** as `PARTIAL` (#52). The plan
  never checked, so a truncated history was indistinguishable from a complete one while
  silently corrupting cycle time, stall detection, item age and WIP.
- **Skill subprocesses run with Waypoint's tokens stripped from their environment**
  (#96). The plan passed the operator's full environment to the `claude` child process.
- **Three of my own decisions were wrong and were reversed.** #59 — an override I wrote
  covered only half the divergence it was meant to close. #68 — I overrode a counter's
  semantics on a misreading, and the implementer was right to push back. #100 — worst of
  all, a precedence rule I set caused the one Critical defect the final review found: a
  fresh skill report silently stripped the `FAILED` badge off the risk register. All
  three are marked as such in place.

## Every decision, in the order it was made

### 1

Ruling: Task 1 writes the UNION — the brief's three precise `.waypoint/` paths (so `.waypoint/reports/` stays trackable, which Task 28 needs) PLUS the existing `.superpowers/` and `.pytest_cache/` lines. Why: the brief's finer `.waypoint/` granularity is deliberate and load-bearing for the reports store, while `.superpowers/` must stay ignored or this skill's own workspace would be committed. Cost if wrong: a stray ignore line; one-line fix.

### 2

Ruling: dispatching Task 1 in parallel with the preflight cross-task conflict scan rather than blocking on it. Why: Task 1 is foundation-only (pyproject, .gitignore, clock.py, errors.py) with complete verbatim code in its brief and "Consumes: nothing"; the scan's value is cross-task interface conflicts, which first bite at Task 2, and I will have the scan in hand well before then. Cost if wrong: at most one small scaffolding commit reworked — and the Task 1 review loop is the designed net for exactly that.

### 3

Ruling: Task 1 Step 6 says `git init` and `git add ... docs/`. Both are already done (repo initialised in session 1; docs committed in c7e1747). Task 1 skips `git init` and does not re-add `docs/`. Why: re-running them is a no-op at best and a spurious diff at worst. Cost if wrong: none.

### 4

Ruling: T1/T20 duplicate .gitignore — Task 20 still writes `.waypoint/.gitignore` as specified. Redundant with the root file but harmless and self-contained. Cost if wrong: one redundant file.

### 5

Ruling: T4/T22 fixture format mismatch — `capture-fixtures` keeps its JSONL per-entity output; connector tests (T7/T8/T9) keep hand-written per-page .json. They serve different purposes: capture-fixtures is an operator capture aid, not the connector-test fixture source. Task 22's help text must say so rather than claim to feed the connector tests. Cost if wrong: an operator hand-converts a captured payload once.

### 6

Ruling: T9/T11 Jira status id — Task 9's `search_page1.json` fixture MUST include `"id"` in the status object (as Task 11's fixture already does). Without it `jira_issues.status_id` is NULL and every board_columns mapping in T16/19/24/25 silently misses. The real Jira API returns the id. Cost if wrong: none, the fixture merely becomes more faithful.

### 7

Ruling: T12/T15 duplicated `_category_map` — Task 15's `flow.py` imports and reuses `derive._category_map` instead of re-implementing it without the `_CATEGORY_HINTS` fallback. Global Constraints already permit `metrics/` to import `waypoint.store.derive`. Resolves the divergence and Section C #15. Cost if wrong: one import to unwind.

### 8

Ruling: T13/T24/T29 register panel status — `report_status` takes precedence over `panel_status(manifest, EVERYTHING)` for the register panel whenever it is set. This is what Task 29's STALE test requires. Cost if wrong: the register panel shows the manifest badge instead of the report badge.

### 9

Ruling: T14/T23 template arithmetic — `{{ bar.track_height + 4 }}` violates the binding "arithmetic in a template is a defect" constraint. `WipBar` gains a precomputed `svg_height` field in Task 14; the `wip_bar` macro uses it. The constraint beats the plan's template code. Cost if wrong: one extra dataclass field.

### 10

Ruling: T14/T23 chart colours — `components/charts.html` must NOT hardcode hexes. Colours reach the macro either as dataclass fields already carrying them (e.g. `fill_color`) or as CSS custom properties defined in `waypoint.css`. This gives Task 14's STROKE_OK/STROKE_P75/TICK/FILL_OK/FILL_NEUTRAL constants real consumers and states the palette once. Resolves Section C #10. Cost if wrong: colour plumbing reworked in one macro file.

### 11

Ruling: T14 palette extension — `DOT_UNDER #4a5260` and `STROKE_P75 #4a7a5e` are permitted despite being absent from the Global palette table. They are chart-internal derived shades, not UI surface tokens. Documented here as the only sanctioned additions. Cost if wrong: two hexes to reconcile.

### 12

Ruling: T18/T26 person panel labels — drop the `|lower` filter in `person.html`; render `panel.label` as authored ("Shipped", "In flight", "Waiting on someone else", "Someone else waiting on them"). Task 18's test asserts the capitalised strings and `|lower` guarantees they never appear. Cost if wrong: heading case.

### 13

Ruling: T20 `run_sync` signature — the implementation signature wins: `run_sync(project_dir, *, cfg, now, sources=None)`. The Interfaces block simply omitted `cfg`, which the plan's own `cli.sync` caller passes. Cost if wrong: one signature line.

### 14

Ruling: T23/T27 sync progress — Task 23's `PageContext` gains a `progress` field defaulting to None, and `deps.page_context` reads `.waypoint/state/progress.json` when present. Without this, Task 27's `sync_state` partial raises UndefinedError on `/`, `/delivery` and `/people`. Cost if wrong: one unused context field.

### 15

Ruling: T23/T25 the `"target" not in body` assertion — as written it can never pass, because `base.html` chrome carries `hx-target` on every page. Its real intent is "no chart carries a goal line". Task 25 replaces it with an assertion scoped to the rendered `<svg>` markup that no goal-line/threshold element is emitted, keeping the WIP tick and the aging threshold as the only reference marks. Cost if wrong: a weaker assertion than intended; the final review sees it.

### 16

Ruling: T23 palette fidelity — `waypoint.css` defines all four fill tokens (`fill-ok #3b6b4f`, `fill-high #5a2f2f`, `fill-med #5c4520`, `fill-neutral #2a3038`) as CSS variables, and `.panel.demoted-failed` uses `#5a2f2f`, NOT the plan's `#5a2a2a`. Global says copy the palette verbatim and that is binding. Cost if wrong: one off-by-a-shade background.

### 17

Ruling: T24/T29 Analyze button count — the assertion must not depend on whether a `claude` binary exists on the machine. Task 29 monkeypatches `claude_available()` in that test so the count is deterministic, rather than the plan's bare edit of `== 1` to `== 2`. Resolves Section C #17. Cost if wrong: a test that passes for the wrong reason on CI.

### 18

Ruling: T28/T29 `Report` field order — declared and implemented orders differ but every construction is keyword-based. Implementation order wins; no action. Cost if wrong: none.

### 19

Ruling: T10 `tests.factories` unimportable — 11 later test modules do `from tests.factories import ...`, which fails under pytest's default prepend import mode with no `tests/__init__.py`. Task 10 creates an empty `tests/__init__.py`. Cost if wrong: caught instantly by Task 10's own test run.

### 20

Ruling: File Structure omissions — `waypoint/doctor.py`, `waypoint/fixtures.py`, `waypoint/store/views.py`, `web/templates/partials/` and `web/routes/analyze.py` are absent from the plan's File Structure section but mandated by Tasks 21, 22, 26, 27 and 29. The tasks win; the File Structure block is illustrative and predates them. Reviewers are told not to flag these as unplanned files. Cost if wrong: none.

### 21

Ruling: T13 metrics import boundary — `metrics/status.py` may import `waypoint.store.manifest` for TYPE references only (the Manifest/EntityStatus dataclasses). It must never construct `ManifestStore` or touch the filesystem. The constraint's stated purpose is to keep filesystem access out of `metrics/`, which a dataclass type import does not breach. Cost if wrong: the dataclasses must be relocated to a types module later.

### 22

Ruling: T24 hours-to-days conversion — `routes/home.py` must not compute `review_wait_current / 24`. The conversion moves into the metric function in `metrics/`, which returns days. `{{ x:.0f }}` formatting stays in the template: formatting is not arithmetic. Cost if wrong: one metric field renamed.

### 23

Ruling: T27 badge vocabulary — the unmatched count renders as a count badge `N UNMATCHED` (uppercase, as a badge), not lowercase panel `right` text. The badge vocabulary in Global Constraints is binding. Cost if wrong: cosmetic.

### 24

Ruling: T27/T29 no-network tests — `test_posting_sync_returns_the_running_partial` and `test_analyze_returns_a_polling_partial` must stub the background work (fake source, fake runner) so no real HTTP request and no real `claude` subprocess is launched. "No test touches the network" is binding. Cost if wrong: a flaky test that hits the network on someone's laptop.

### 25

Ruling: T4 `EntityStatus.extra` — drop the sixth field. It is absent from the task's own Interfaces block and never read anywhere in the plan. YAGNI. Cost if wrong: re-add one field.

### 26

Ruling: C1 five-fold verbatim SKILL.md duplication (T30) — KEEP as mandated. These are five independently-shipped standalone documents; duplication across separately-distributed artefacts is correct, not DRY damage. Reviewers pre-warned. Cost if wrong: five files to refactor into an include.

### 27

Ruling: C19 `PRAGMA foreign_keys = ON` with no FK constraints (T10) — KEEP. A one-line correctness default that costs nothing and is right if the schema ever gains FKs. Cost if wrong: one redundant pragma.

### 28

Ruling: the remaining Section C items are real defects and are fixed in the task that introduces them, as follows —   C2 (T15): drop the unused `DONE` from the derive import; the plan itself     sanctions this ("if the linter objects, drop it").   C3 (T20): drop the unused `signal` import from `sync.py`.   C4 (T24): drop the unused `IN_PROGRESS` import from `routes/home.py`.   C5 (T13): replace `assert hash(status) is not None` (cannot fail) with a real     assertion — insert into a set, and assert FrozenInstanceError on assignment.   C6 (T25): the window-selector test must assert the 26w option is MARKED     current/selected, not merely present — all four options always render.   C7 (T26): assert the `since` input's VALUE equals the remembered window, not     that the string "since" appears.   C8 (T14): `min(float(width), count * (width / max(count, 1)))` is identically     `width`; simplify the no-limit branch to `fill_width = float(width)`.   C9 (T14): assert against `charts.MED` / `charts.HIGH`, not inline hexes.   C11 (T21): drop `StubGithub.reachable()` / `StubJira.reachable()` if     `run_checks` does not call them. If the brief's prose shows doctor genuinely     needs a reachability probe, wire the call instead of deleting — implementer's     judgement, but no orphaned stub methods either way.   C12 (T20): `github_source()` passes `tuple(statuses.keys())` explicitly for     `entities` rather than relying on `tuple(dict)`.   C13 (T29): use a normal `from waypoint import clock` import, not     `__import__("waypoint.clock", fromlist=["clock"])`.   C14 (T28): replace the bare `[11:]` slice with a named constant     (`_DATE_PREFIX_LEN = len("YYYY-MM-DD-")`).   C16 (T29): `_running` must not be a module-level dict shared across every     `create_app` instance — hang it on the app instance (`app.state`) so tests     cannot leak state into each other.   C18 (T10): SQLite files are not byte-stable; replace     `read_bytes() == first` with a content-level idempotence assertion     (deterministic query results / per-table row counts).   C20 (T17): move the `weight(row)` closure out of the per-epic loop to module     level.   C21 (T20/T22): annotate `_fail` as `-> NoReturn` so the possibly-unbound     locals after it are provably unreachable.   C22 (final verification): widen the template-arithmetic grep from `[*/]` to     cover `+` and `-` as well. Combined with the C-ruling on T14/T23 above, the     gate then actually catches what it was written to catch.   C23 (T24): extract the inline `'%block%'` SQL literal to a named module     constant.   C24 (T23/T29/T30): replace cwd-relative paths with paths derived from     `__file__` (tests) and from a resolved project root (T30 `SKILLS_DIR`).   C25 (T19): `column_over_limit` risks must carry the real age of the oldest     item in the column, not the placeholder `age_days=0.0, age_text=""` — the     register's sort key and right-hand column are otherwise meaningless.   C26 (T25/T26/T27): move the inline `style="..."` attributes into     `waypoint.css` classes. Cost if any of these is wrong: a localised revert in one task, visible in the final whole-branch review.

### 29

Ruling: T6 429 classification — `classify()` must return `kind="rate_limit"` for HTTP 429, not `kind="http"`. The test states the intent, and actionable rate-limit errors are a spec goal. Fix `classify()`, not the test. Cost if wrong: one branch.

### 30

Ruling: T6 rate-limit reset timestamp — the plan's expected string `2026-08-18T05:46:40Z` does not match epoch `1787200000`. Arithmetic wins: the implementer computes the value with `datetime.fromtimestamp(1787200000, UTC)` and asserts THAT. Cost if wrong: none; it is a computation.

### 31

Ruling: T13 partial-reason arithmetic — test asserts `"62" in reason` while the fixture's `arrived` counts sum to 69. The implementer computes the true sum from the fixture and asserts that number. Cost if wrong: one literal.

### 32

Ruling: T15 throughput test setup — the test seeds `issue_transitions` but `throughput()` reads `issue_flow.first_done_at`, and `derive_all` is never called, so it measures 0/0. The test must call `derive_all` after seeding. Deriving is a build step, not an implicit side effect of insertion. Cost if wrong: one setup line.

### 33

Ruling: T15 wip_series fixture ambiguity — both issues use the default `status="In Progress"` while the assertions need different categories, which no `_category_map` resolution can satisfy. Give the two issues DISTINCT status names consistent with their intended categories. Cost if wrong: fixture names.

### 34

Ruling: T18 `insert_pr` factory defect — it leaves `created_at`/`updated_at` NULL, so PRs fall out of every windowed query and `test_workstreams_touched` measures 2 where it expects 3. The factory must default both columns to non-NULL values derived from the test's `now`. This is a shared-factory fix in `tests/factories.py` (Task 10) and binds every later task that inserts PRs. Cost if wrong: many windowed assertions shift; caught by the task suites.

### 35

Ruling: T19 `evaluated` double-count — the PR loop and the limited-column loop both increment it, giving 2 where the test expects 1. `evaluated` counts each RULE once, not each loop iteration. Reconcile code and assertion on that definition and state which in the report. Cost if wrong: a counter's meaning.

### 36

Ruling: T19 missing tenth risk rule — the Interfaces block promises all ten rules from spec §11 and only nine are implemented. The spec is binding: the implementer reads §11 and implements the missing rule. Cost if wrong: a risk rule nobody asked for; the final review catches it.

### 37

Ruling: T19 `escalate` vs `threshold_widths_over` — the implementation name wins unless a later task actually calls `escalate`. The implementer greps for callers before choosing. Cost if wrong: one rename.

### 38

Ruling: T21 doctor checks — the Interfaces block lists nine checks and `run_checks` emits five. Implement all nine, including the GitHub/Jira reachability probes. This SUPERSEDES my earlier C11 ruling: the stub `reachable()` methods are not orphans, they are the consumers of these checks. Reachability must be stubbed in tests — no test touches the network. Cost if wrong: four extra doctor checks.

### 39

Ruling: T23 ui macro names — Interfaces say `panel`/`section_label`/`row`; implementation defines `panel_open`/`panel_close`/`item_row`. Implementation names win — they are what every later template actually calls. Cost if wrong: one rename across templates.

### 40

Ruling: T23 `now` template global — do NOT register `now` as a Jinja global even though the Interfaces block lists it. A clock in the template namespace invites exactly the date arithmetic the Global Constraints forbid, and nothing renders it. Only `charts` is registered. Cost if wrong: one global to add if a template genuinely needs it.

### 41

Ruling: T24 unsatisfiable assertion — `assert "open PRs" in body.lower()` can never pass. Lowercase the needle: `"open prs" in body.lower()`. Cost if wrong: none.

### 42

Ruling: T30 test count — the brief says "PASS, 40 tests (parametrized)" while the listed suite totals 36. The count is informational, not a requirement; the implementer asserts nothing about it and reports the true number. Cost if wrong: none.

### 43

Task 3: Ruling: reviewer raised an Important plan-mandated finding — `Person.id` is derived from the display name, so a rename silently changes the id, and the duplicate-name suffix (-2, -3) is assigned by config-file order rather than a stable key. I checked the blast radius: later briefs hard-code the name-derived slugs in 20+ places (`by_id("alex-rivera")` x7, `/people/alex-rivera` x7, `person_id="alex-rivera"` x3, `bo-chen` x3). Decision: KEEP the brief's derivation. Human-readable person slugs in URLs are the evident design intent, no binding Global Constraint requires a stable opaque id, and re-deriving it now would desynchronise 27 downstream briefs whose fixtures assume these exact slugs. A person rename is therefore a config-migration concern for the human, not a correctness bug in this task. Cost if wrong: renaming a person orphans their stored reports and changes their URL — recoverable by hand, and surfaced to the human in the final ruling list.

### 44

Task 3: parked — Person.id name-derivation and order-sensitive dedup — Ruling above; the code stands.

### 45

Ruling (refines the T6 429 ruling, now verified against the brief's code): the brief's `classify()` has branches for 401, 403, 404, >=500 and a fallthrough — no 429 branch. So `test_exhausted_retries_raise_a_rate_limit_error_naming_the_wait` (429 + `Retry-After: 3`, empty body) fails BOTH assertions: `.kind` is "http" not "rate_limit", and `.message` is `"/thing returned 429: "` which does not contain "3". Fix: add an explicit `status == 429` branch to `classify()` returning kind="rate_limit" with a message that names the Retry-After wait. The 403-with-rate-limit-signal branch stays as it is. Cost if wrong: one branch.

### 46

Ruling (confirms the T6 reset-timestamp ruling with computed evidence): epoch 1787200000 is `2026-08-20T04:26:40Z` — I ran the conversion. The brief's expected `"2026-08-18T05:46:40Z"` at test line 126 is simply wrong and the test cannot pass. The implementer corrects the expected literal to `2026-08-20T04:26:40Z`. Cost if wrong: none, it is arithmetic.

### 47

Task 6: Ruling: reviewer found an Important plan-mandated defect — the brief's `_retry_after_seconds` clamps a past HTTP-date Retry-After to 0.0 but does NOT clamp a negative NUMERIC one, so `Retry-After: -5` flows to `time.sleep(-5)` and raises an unhandled ValueError instead of the actionable SourceError the spec mandates. Reviewer confirmed the crash by direct test. Decision: FIX it (clamp the numeric branch with max(0.0, ...) exactly as the date branch already does). Why: the spec's "errors must be actionable" constraint is binding, an unhandled ValueError in the retry loop is a genuine correctness bug, and the fix is one line with no downstream interface change. Cost if wrong: none — clamping a negative delay to zero is strictly safer than passing it through.

### 48

Task 7: Ruling: reviewer found an Important plan-mandated spec violation — github.py:171-177 does `if not login: continue  # team review requests are not attributed to a person`, silently discarding every review request whose requestedReviewer is a Team rather than a User (PULL_REQUEST_QUERY only resolves `... on User { login }`). Spec §9 "activity is never dropped" is a binding Global Constraint and this is precisely the case it exists for. Decision: FIX. Extend PULL_REQUEST_QUERY with a `... on Team { slug }` inline fragment and emit the record with the login component as `team:{slug}` (falling back to "unknown" if neither resolves), so the timeline event survives into the raw store. Downstream, roster.resolve_github maps an unrecognised login to UNATTRIBUTED, which is exactly the designed behaviour: unattributed work is counted, not lost. I checked the blast radius first — neither fixture contains a team review request, so no existing assertion changes. Cost if wrong: review request records gain a `team:`-prefixed id form that later tasks must tolerate; contained to one id component, and strictly better than losing the event.

### 49

Task 8: Ruling: the brief's verbatim `_rest_records_for` reads only REST's `requested_reviewers` and `continue`s on a missing login — it never reads `requested_teams`. That reintroduces on the REST path the exact spec §9 defect ("activity is never dropped") I ruled must be fixed on the GraphQL path in Task 7. The implementer followed instructions and escalated rather than papering over it; credit to it for that. Decision: FIX, for the same binding reason as Task 7, and for the additional one that the two transports must agree — a team review request that exists under GraphQL and vanishes under REST would make the dashboard's numbers depend on which transport the GHE instance happened to support. Cost if wrong: REST review-request records gain team: ids, same contained blast radius as Task 7's fix. Task 8: override applied — commits 21fc0e3 (impl) + 0ee4620 (fix: REST review requests never drop team requesters). 72/72 passing; RED reproduced via stash (IndexError — team request absent pre-fix), GraphQL tests unaffected (14/14).

### 50

Task 8: Ruling: reviewer disproved the implementer's REST/GraphQL id-parity claim, and digging in, the real defect is worse than the id. GraphQL takes `requested_at` from the actual ReviewRequestedEvent (`event["createdAt"]`); REST takes it from `node["created_at"]` — the PULL REQUEST's creation time, which is not when the review was requested. REST simply does not expose a per-request timestamp; that is precisely why spec §8 makes GraphQL the primary path ("review latency needs each PR's reviews and review requests"). So the brief fabricates a plausible-looking but wrong timestamp. I verified the downstream consequence: Task 10's `pr_review_requests` has `PRIMARY KEY (pr_id, requested_login, requested_at)` and the payload's requested_at flows straight into it, so (a) a transport switch double-counts one real request as two index rows, and (b) far worse, review-wait metrics computed over REST-sourced data would silently use the PR open time — a PR opened in May with a review requested in August would report a three-month review wait. That violates "never render silently-incomplete data", the constraint the whole degradation design exists to serve. Decision, three parts:   1. REST must NOT fabricate requested_at from the PR creation time. It emits      the literal "unknown" in both the id slot and the payload field, so the      absence is explicit and no metric can mistake it for a real request time.   2. Task 10 (carried into its dispatch as a binding override) must dedup      review requests by (pr_id, requested_login), preferring a row with a real      timestamp over an "unknown" one. That kills the double-count at the index      without changing the raw id contract.   3. `probe_graphql` must re-raise when the SourceError kind is "auth" and      return False otherwise, so "GraphQL unsupported, fall back to REST" is      distinguishable from "your token is bad" — which the task's own Interfaces      block requires and the blanket `except SourceError: return False` defeats. Cost if wrong: REST-sourced review requests carry no usable request time, so review-latency panels degrade rather than show a number when a GHE instance lacks GraphQL. That is the honest outcome and matches §8's own rationale; the alternative is a confidently wrong metric.

### 51

Task 9: Ruling (finding 1): the status-id override I mandated has no regression test — the connector preserves status.id only by construction (it yields the issue dict unmodified), and nothing pins it. Since the entire reason for the override is that a missing status.id silently empties every board column in Tasks 16/19/24/25, a future tidy-up of _fetch_issues could reintroduce the exact defect with the suite still green. Decision: FIX — add an explicit assertion on payload["fields"]["status"]["id"]. Cost if wrong: one assertion.

### 52

Task 9: Ruling (finding 2, plan-mandated): the connector yields `issue["changelog"]` as-is and never inspects the `startAt`/`maxResults`/`total` that Jira embeds beside `histories`. Jira caps embedded changelog history, so when total > len(histories) the history has been silently truncated and is indistinguishable from a complete one. The changelog is the SOLE source of cycle time, stall detection, item age and historical WIP (§8), so silent truncation corrupts all four — and "never render silently-incomplete data" is a binding constraint. Decision: FIX, but scoped to DETECT AND REPORT, not to build pagination. When total > len(histories), the `changelogs` entity is marked `partial` with an actionable error naming the affected issue keys. That routes the problem into the manifest/degradation machinery that already exists for exactly this purpose — the UI shows PARTIAL and demotes the panels that read it — without inventing an unplanned changelog-pagination feature the plan never scoped. Cost if wrong: boards with deep issue histories show PARTIAL rather than silently-wrong cycle times; that is the honest failure and the spec's stated preference.

### 53

Task 12: Ruling: implementer flagged that the brief's Files line says "Modify index.py ... export IN_PROGRESS/DONE" but the brief's own index.py code block adds only the derive_all call, with no re-export. I grepped every later brief: all five import sites are `from waypoint.store.derive import ...` — none imports these constants from `index`. Decision: NO re-export. The Files line is stale prose; adding an unused re-export would be dead code a reviewer would rightly flag. The implementer copying the code block verbatim rather than inventing an export was correct. Cost if wrong: one import line if some later task turns out to want them from index.

### 54

Task 12: Ruling: reviewer found `_derive_pr_flow` computes all four hour columns via bare hours_between() with no ordering check, while `_derive_issue_flow` directly below it has an explicit `first_done >= first_in_progress` guard. A review submitted after merge is a real GitHub scenario (as are rebased/backdated commit timestamps), and it would render as a NEGATIVE time-to-first-review — a confidently wrong number on the dashboard, which the spec forbids. The asymmetry is the tell: the plan knew about the risk and guarded only one of the two. Decision: FIX — guard at the pr_flow computation sites so an end preceding its start yields None (unknown), mirroring issue_flow's explicit style. Deliberately NOT fixing inside `hours_between` itself: that helper is imported by Task 15, and silently converting negatives to None there could mask a genuine bug in some other caller. Explicit at the call site is clearer and better scoped. Cost if wrong: an anomalous PR shows a blank rather than a negative duration — the honest rendering either way.

### 55

Ruling (refines the T13 partial-reason ruling with the code in hand): the brief computes `arrived = sum(e.count for e in known)` over ALL entities in the group (62 + 5 + 2 = 69) while its own test asserts `"62" in status.reason`. Beyond the mismatch, the sentence is incoherent as written — it pairs a group-wide record count with `error_text` drawn only from the entities that actually failed: "69 records arrived before the fetch stopped: rate limited at page 4", where 7 of those 69 came from entities that completed fine. UI§6 says PARTIAL states how much arrived and why IT stopped; the "it" is the fetch that stopped short. Decision: `arrived` sums the counts of entities whose status is NOT ok — the ones that actually stopped — giving 62 and making the sentence true. The brief's test literal stands; the implementation is what is wrong. Cost if wrong: the PARTIAL reason under-reports total records fetched for the panel, which is the more useful number anyway.

### 56

Task 13: Ruling (finding 1, Important, plan-mandated): when a panel group has a missing (never-synced) entity AND a known entity that genuinely failed, the brief returns ONLY the "has never synced" reason and drops the real error entirely. A 401 on github/review_requests would vanish from the user-facing sentence because github/reviews was never configured. UI§6 requires FAILED to name the source AND the error; a diagnostic that hides the actionable half is the exact failure the constraint exists to prevent. Decision: FIX — the FAILED reason reports both the missing entities and the errors from known-failed ones in a single sentence. Cost if wrong: a slightly longer reason line.

### 57

Task 13: Ruling (finding 2, reviewer filed as Minor "currently dormant" — it is NOT dormant): `_UNAFFECTED` maps github -> "Jira panels are unaffected." and jira -> "GitHub panels are unaffected.", so a group spanning both sources concatenates them into "Jira panels are unaffected., GitHub panels are unaffected." — telling the user GitHub is fine while GitHub entities are in the very group that just failed. The reviewer judged it unreachable, but I grepped the later briefs: panel_status(..., EVERYTHING) appears at FOUR call sites (task-24:283, task-25:208, task-26:247 and :283). It goes live the moment the web layer lands. Decision: FIX NOW — when the entity group spans more than one source, omit the "unaffected" clause entirely, because nothing is unaffected. Fixing it here is far cheaper than three later tasks inheriting a self-contradictory sentence. Cost if wrong: mixed-source failures lose a reassurance clause that would have been false anyway.

### 58

Task 15: Ruling: `test_distribution_of_an_even_sample` asserts p75 == 3.5 for [1,2,3,4], but the brief's own distribution() uses statistics.quantiles(method="inclusive"), which gives 3.25. I ran all three candidate methods against BOTH conflicting tests:   [1,2,3,4]          -> inclusive 3.25, exclusive 3.75, Tukey 3.5   [4,8,12,40]        -> inclusive 19.0, exclusive 33.0, Tukey 26.0 `test_review_latency_reports_median_and_p75_in_hours` requires p75_text == "19h", which ONLY inclusive produces. So no single method satisfies both assertions — the implementer's finding is exactly correct. Decision: KEEP method="inclusive" and correct the unit test's literal from 3.5 to 3.25. Why: inclusive is what the implementation uses and what the domain-meaningful panel test requires; the even-sample test is a hand-computed expectation that used Tukey's hinges by mistake. Changing the method instead would break the review-latency panel, which is the actual product behaviour. Cost if wrong: a unit test documents 3.25 as the p75 convention; the panel numbers are unaffected either way.

### 59

Task 15: Ruling: reviewer found Override 1 only HALF met, and it is right — this is a gap in how I wrote the override, not just in the implementation. I told the implementer to import `_category_map` instead of re-implementing it, but the actual divergence risk lived in the LOOKUP, not the map: `derive._category_for` consults `_CATEGORY_HINTS` ("closed"->DONE, "resolved"->DONE, "to do"->TODO) before defaulting, whereas `flow._wip_at` does a bare `categories.get(to_value, IN_PROGRESS)`. Since `_category_map` is built from `SELECT DISTINCT status FROM jira_issues` — only statuses some issue CURRENTLY holds — any status appearing in transition history but not in the present snapshot (a deprecated "Closed", say) is absent from the map. `_category_for` would still classify it DONE via the hint; `_wip_at` silently calls it IN_PROGRESS and inflates the WIP count. That is exactly the cross-module disagreement Override 1 existed to close, and no test catches it because both fixture issues' transition targets happen to be in the current snapshot. Decision: FIX — `_wip_at` uses `derive._category_for`, not a raw dict get. Cost if wrong: WIP series counts change for boards with retired status names, which is the correction, not a regression.

### 60

Task 16: Ruling: reviewer flagged `import json` in metrics/board.py as absent from the Global Constraints' enumerated metrics/ allow-list. Decision: PERMITTED, and this ruling stands for every remaining metrics/ task. The allow-list exists to keep I/O, HTTP and web frameworks out of metrics/ — it is not a closed set of stdlib modules. `flow.py` and `status.py` already import `timedelta`, `collections.abc.Sequence` and `typing.TYPE_CHECKING`, none of which are listed either. `json` is needed to decode the status_ids JSON column and performs no I/O. The binding prohibitions remain: no httpx, no fastapi, no jinja2, nothing under web/, and no filesystem access. Cost if wrong: a stdlib import to unwind.

### 61

Task 17: Ruling: reviewer flagged that epics with zero children are dropped entirely (the `parents` query groups by parent_key, so a childless epic yields no row), and asked whether that conflicts with spec §9 "activity is never dropped". It also noted the brief contains an explicit test, `test_an_epic_with_no_children_is_not_listed`, asserting exactly that. Good question to raise rather than assume. Decision: KEEP the behaviour; no fix. Reasoning: §9 protects ACTIVITY RECORDS — work items, reviews, transitions — from being silently lost, which is why the team-review-request and changelog-truncation findings were real violations: data that existed was disappearing. A childless epic contains no activity to drop; nothing is hidden by its absence. Listing it would render a meaningless "0 / 0" completion and a projection computed from no basis, which would itself violate "never render silently-incomplete data". The section is defined as epics with child issues and its empty_message says so, and the brief's explicit test shows this was a deliberate scoping decision rather than an oversight. Cost if wrong: an empty epic never appears on the delivery page; a manager wanting epic-hygiene signals would need a separate feature the plan never scoped.

### 62

Task 17: parked — childless epics excluded from the epics section — Ruling above; the code stands.

### 63

Task 18: Ruling (Important, plan-mandated): people.py coalesces unknown durations into zero with `... or 0.0` in four places (lines 147, 159, 236, 264). Two harms: it renders a FABRICATED "oldest 0d" where the truth is "unknown", and it feeds that fake 0 into the threshold check, so a genuine worth-asking-about case is silently never flagged. The reviewer traced a concrete, non-theoretical trigger: derive.py sets `pr_flow.review_wait_current` to NULL as soon as ANY non-author non-bot reviewer submits, while `owed`/`awaiting` are scoped to whether THE TRACKED PERSON has reviewed — so on any multi-reviewer PR where someone else reviewed and this person has not, the wait goes NULL and the card shows "0d" and stays unflagged. This is the same species as Task 8's fabricated `requested_at`: substituting a plausible number for an unknown. It also defeats the one mechanism that justifies emphasis="med" existing at all. Decision: FIX — never render a coalesced 0 for an unknown duration, and never compute a threshold crossing from one. Scoped deliberately: do NOT redesign per-reviewer wait tracking (real per-person review latency is a feature the plan never scoped); just stop lying about what is unknown. Cost if wrong: cards show a count without an age clause where the age is genuinely unknown — the honest rendering.

### 64

Task 18: Ruling: reviewer raised BUCKET_COLORS mapping bug->"high" (the danger token) on a person page as a possible red/green judgement of a person. I checked the UI spec: line 135 states verbatim "Buckets are coloured `ok` / `high` / `med` for feature / bug / toil". The spec is the binding authority and is explicit, so the mapping STANDS unchanged. Recording it because it is a values-adjacent choice the spec made deliberately and the human may want to revisit it: on a person page, bug work rendering in the danger red can read as a judgement even though no comparison is present. Cost if wrong: a colour token, changeable in one dict.

### 65

Task 18: Ruling on the re-reviewer's two out-of-scope observations — both are non-issues given context it lacked, so neither extends the loop and neither is a deferred minor:  (a) It flagged `INNER JOIN pr_flow`/`INNER JOIN issue_flow` as silently      excluding rows with no flow row. But Task 12's `derive_all` iterates an      unfiltered `SELECT * FROM pull_requests` / `SELECT key FROM jira_issues`      and emits exactly one flow row per PR and per issue, unconditionally — that      was verified in Task 12's own review. There is always a matching row, so      the INNER JOIN can exclude nothing.  (b) It flagged the `waiting_on_others` panel filtering      `review_wait_current IS NOT NULL` as dropping activity. Semantically that      filter is CORRECT for this panel: review_wait_current goes NULL precisely      when a review has arrived, and a person whose review arrived is by      definition no longer waiting on someone else. Excluding those PRs is the      panel's meaning, not a silent drop.

### 66

Ruling (refines the T19 rulings with the text verified): I read spec §11's table and enumerated the brief's Risk() constructions. §11 lists TEN rules; the brief implements NINE — pr_no_review, pr_approved_unmerged, issue_flagged, issue_stalled, issue_unassigned, issue_aging, issue_reopened, column_over_limit, epic_single_owner. The missing rule is §11's "Epic projected to finish past its due date | escalates with drift". Task 17 already computes `projection_state` and `drift_days` on EpicRow, so the data needed is sitting there. Decision: implement it as `epic_drift`. Cost if wrong: one extra risk rule, removable.

### 67

Ruling: the preflight reported `escalate` vs `threshold_widths_over` as a function-NAME conflict. Reading the brief, the function is named `escalate` in both the Interfaces block and the implementation — only the PARAMETER differs (`days_over` declared, `threshold_widths_over` implemented). And the implementation's name is the correct one: every caller passes `days / threshold - 1`, which is threshold-widths-over, not days-over. Decision: keep `threshold_widths_over`; the Interfaces block's `days_over` is simply the wrong name for what is passed. No conflict to resolve. Cost if wrong: none.

### 68

Task 19: Ruling: MY OVERRIDE 3 WAS WRONG and the implementer was right to push back. I told it `evaluated` counts each RULE once. Reading the brief properly: the test is named `test_an_empty_register_reports_how_many_items_were_evaluated` — ITEMS, not rules — and all four increment sites count a distinct examined item (per open PR, per in-flight item, per limited column, per epic). That is a coherent, correct design and there was nothing to fix. On the value: the shared `con` fixture inserts an "In Progress" column with limit 2 (counted) and a "Blocked" column with no limit (correctly skipped by the §11 no-limit rule), so the test's 1 open PR plus 1 limited column gives evaluated == 2. The brief's assertion of 1 is simply wrong. Decision: `evaluated` keeps its per-item semantics and the four increment sites stand; the assertion is corrected to 2. The implementer hand-traced this instead of forcing my override through, which is exactly right. Cost if wrong: a counter's stated meaning; the register's contents are unaffected either way.

### 69

Task 19: Ruling: `epic_drift` needs a threshold width to escalate against and neither the spec nor `Thresholds` supplies one — §11 says only "escalates with drift", while the other four escalating rules each divide by a configured threshold. The implementer chose a named `EPIC_DRIFT_WIDTH_DAYS = 7` (one week of schedule slip per severity step). Decision: ACCEPT, provided it is a named module constant with a comment saying it is a chosen default rather than a spec value. Adding a fifth `Thresholds` field would ripple through Config, the conftest CONFIG_TOML and every fixture — scope creep the plan never asked for. Surfacing it here so the human can promote it to config if they want it tunable. Cost if wrong: epic-drift severity steps at the wrong cadence; one constant. Task 19: PROCESS ERROR (mine): I withdrew Override 3 in this ledger and stated the withdrawal in the REVIEW dispatch, but never sent the revert instruction to the implementer. So the implementer — correctly following the override it was actually given — built the per-rule dedup set, and the reviewer then flagged the code for contradicting a ruling the implementer had never received. The finding is valid; the fault is in my sequencing, not the implementer's work.

### 70

Task 19: Ruling (confirming the withdrawal, having reconsidered rather than just flip-flopping): per-ITEM is the right semantic and per-rule is not. A per-rule count is bounded at ten and is very nearly constant, so it tells the reader almost nothing. A per-item count is what makes the empty state meaningful — "Nothing crossed a threshold" alongside evaluated=47 says the register is empty because nothing qualified, not because nothing was checked. The brief's test name (`..._how_many_items_were_evaluated`) agrees. Decision: revert to the brief's unconditional `evaluated += 1` at the four examination sites; restore the test name; and the dedup test either asserts 4 (reviewer hand-traced: 3 open unreviewed PRs + 1 limited column) or is dropped, since it encodes behaviour the ruling rejects. Cost if wrong: a counter's meaning.

### 71

Task 21: Ruling: implementing the two reachability checks and the repo/board readable checks required adding `reachable()`/`repo_readable()` to GithubSource and `reachable()`/`board_configuration_readable()` to JiraSource, extending those connectors past their own briefs' interfaces, and extending the StubGithub/StubJira doubles to match. ACCEPTED — this is the necessary consequence of the override, and the alternative is worse: doctor issuing raw HTTP itself would put transport concerns outside `sources/`, breaking the module boundary the design exists to enforce. Keeping the probes on the source objects means doctor tests can stub them, which is what keeps the suite off the network. Cost if wrong: four methods on the connectors that only doctor calls.

### 72

Task 21: Ruling: the implementer chose the reachability endpoints itself — `GET /api/v3/user` (GitHub) and `GET /rest/api/3/myself` (Jira) — since neither the brief nor my override named one. ACCEPTED. Both are the canonical "am I authenticated and can I reach you" identity probes for their APIs, both are GETs (satisfying the strictly-read-only constraint §3), and both are cheap and unpaginated. Cost if wrong: two URL constants.

### 73

Task 21: Ruling (Important): no doctor-level test exercises the graphql auth-failure path. StubGithub.probe_graphql can only return a bool, never raise, so doctor's `except SourceError` branch for the graphql check is untested — and that branch IS the auth-vs-unavailable distinction Task 8 was fixed to provide. The reviewer hand-verified the behaviour is correct, but an untested distinction is one a later refactor silently loses. Decision: FIX — add a stub variant that raises SourceError(kind="auth") and assert the graphql check comes back ok=False with the auth message, not the "unavailable, will use REST" text.

### 74

Task 21: Ruling (Minor, folded into the same fix because it is the same stub class and the same test file): GithubSource.reachable() and JiraSource.reachable() only ever return True or raise — they never return False against a real connector — yet StubGithub(reachable=False)/StubJira(reachable= False) model exactly that impossible outcome, and doctor carries an unreachable `False` branch to handle it. A test double that models a failure mode the real class cannot produce makes its tests prove nothing about reality. Decision: make the doubles model the REAL contract (raise SourceError) and drop the dead branch. Cost if wrong: reachability failures all arrive as SourceError, which is what the real connectors actually do.

### 75

Task 22: Ruling (2 Important): the runtime behaviour is correct but the security boundary has NO CI protection. (a) Only 2 of the 6 brief-enumerated attack shapes have committed tests; PRAGMA, ATTACH, VACUUM and — most importantly — the CTE-write bypass that the read-only connection alone catches have zero regression coverage. The implementer verified them with a throwaway uncommitted script, so a future change to connect() or the guard string would silently lose the backstop with nothing in CI noticing. (b) `capture()` — the pipeline that actually walks raw records and writes redacted fixtures to disk — has no test at all; only `redact()` is tested in isolation, so the wiring that the "tokens are never written to captured fixtures" constraint is actually about is unverified end to end. Decision: FIX both. These are cheap tests guarding the product's only arbitrary-SQL surface and its credential-redaction pipeline — precisely the code where a silent regression is most expensive. Cost if wrong: a few extra tests.

### 76

Ruling (refines the T14/T23 chart-colour ruling with the text in hand): I said earlier that routing chart colours through CSS custom properties would give Task 14's STROKE_OK/STROKE_P75/TICK constants real consumers. That was wrong — CSS vars give a Python constant no consumer at all. Better answer, available because the Interfaces block already registers the `charts` MODULE as a Jinja global: the macros interpolate `charts.STROKE_OK`, `charts.TICK` and so on directly. That states each hex exactly once (in charts.py), gives every exported constant a real consumer, and is precisely the "interpolate a precomputed value" pattern the constraint asks for — no arithmetic, no duplication, no new dataclass fields. Confirmed hardcoded literals to replace in components/charts.html: #1c2028 (x2), #3b6b4f, #4a7a5e, #5c4520, #7dd3a0, #8b93a1.

### 77

Ruling (confirms the T23 macro-naming ruling with call-site evidence): the Interfaces block declares `panel`/`section_label`/`row`; the implementation defines `panel_open`/`panel_close`/`item_row`. I grepped the later briefs: ui.panel_open x13, ui.panel_close x13, ui.item_row x4, and ZERO calls to ui.panel, ui.section_label or ui.row. The implementation names win decisively. `section_label` is declared but never defined and never called — do not implement it (YAGNI).

### 78

Ruling: `bar.track_height + 4` appears THREE times in the wip_bar macro (viewBox, the height attribute, and the tick line's y2). All three become `bar.svg_height`, the field Task 14 added for exactly this.

### 79

Ruling: `.panel.demoted-failed` uses `#5a2a2a`; the palette's fill-high is `#5a2f2f`. Corrected to the palette value, which Global Constraints require verbatim.

### 80

Task 23: Ruling: HTMX was vendored from GitHub raw rather than unpkg (the sandbox blocked unpkg), same v2.0.4 dist/htmx.min.js, 50,917 bytes. ACCEPTED. I verified the asset myself: it carries the htmx signature and contains ZERO external URLs, so it cannot phone home — which is the property the offline constraint (UI§9) actually cares about. Recording its hash so the human knows exactly what was vendored:   sha256 e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447 Cost if wrong: a third-party asset to re-vendor from a preferred mirror.

### 81

Task 23: Ruling: `#1c2028` (the WIP track background) has no matching constant in charts.py — it is the palette's `border-dim`, not one of the FILL_* values — so the implementer used `fill="var(--border-dim)"`, the CSS token. ACCEPTED: that is exactly the "nearest correct mechanism" my override allowed for this case, it states the hex once (in the stylesheet), and inventing a new Python constant for a background shade would be worse. Cost if wrong: one attribute.

### 82

Task 23: Ruling (Important, plan-mandated): `page_context` calls `load_config(root)` unguarded on EVERY request, and `load_config` raises ConfigError when `.waypoint/config.toml` is absent. No route or app-level handler catches WaypointError, so running `waypoint serve` in a directory that has never been configured 500s on every page instead of showing the first-run empty state this task built `empty.html` for. The tests miss it because the project_dir fixture always writes a valid config, so they only ever exercise "configured but not synced". Decision: FIX. Serving before creating a config is a completely ordinary first action, a stack trace is the worst possible first impression, and "errors must be actionable" is a binding constraint the rest of this product honours everywhere else. Scoped to an app-level WaypointError handler rendering an actionable page — not a redesign of startup. Cost if wrong: one handler and one test.

### 83

Task 24: Ruling: I am REGRADING the reviewer's third Minor to Important and fixing it. `review_wait_text = f"..." if wait else "reviewed"` treats a review_wait_current of exactly 0.0 the same as None, so a PR that just became ready with zero wait renders **"reviewed"** — a factually false statement, since nobody has reviewed it. That is not a polish nit: it is the same unknown-vs-zero conflation I fixed in Task 6 (`if delay else`), Task 12 (negative durations) and Task 18 (unknown rendered as "0d"), and it puts a wrong claim on screen, which "never render silently-incomplete data" forbids. The bug is verbatim from the brief and merely travelled into metrics/ during Override 1, but relocating it was this task's job. Fix: `if wait is not None`. Cost if wrong: a just-opened PR reads "0d waiting" instead of "reviewed" — which is the truth.

### 84

Task 24: Ruling: reviewer's first Minor — `more=max(0, len(items) - VISIBLE_ROWS)` in the route — ACCEPTED as-is, no fix. The "web never computes" constraint targets DOMAIN computation: percentages, SVG coordinates, date differences, unit conversion. Counting how many list rows overflow a display cap is presentation bookkeeping, not a metric, and pushing it into metrics/ would put a UI truncation constant in the metrics layer — worse for the boundary, not better. Cost if wrong: one small helper.

### 85

Task 26: Ruling: `ctx.now - timedelta(days=14)` in the person route is a date difference, which the "web never computes" constraint names explicitly. ACCEPTED as-is. The distinction I am drawing, consistent with the Task 24 ruling on `max(0, len(items) - VISIBLE_ROWS)`: computation that produces WHAT THE USER SEES belongs in metrics/; computation that selects WHAT TO ASK FOR is routing. The `/ 24` I moved out of the home route in Task 24 produced a displayed string ("9d waiting"); this subtraction produces a query bound passed as the `since` argument to person_view, and nothing renders from it directly — the displayed window comes back as `view.window_from`, which metrics produces. Somebody has to choose a default window, and a route choosing its own request defaults is ordinary. Cost if wrong: a `default_since()` helper in metrics/ and one import.

### 86

Task 26: Ruling (Important 1, plan-mandated): the roster route computes `panel_status(ctx.manifest, EVERYTHING)` into the context as "status", but people.html NEVER references it. So the roster cards — which display PR and issue counts drawn from the very tables that status checks — carry no badge and no reason when the underlying sync is PARTIAL or FAILED. Every other data block in the app wraps itself in panel_open(..., status); this is the only one that does not. That is a direct §4 "never render silently-incomplete data" violation, and status.py's own docstring calls it "the only failure mode that causes real harm, because the user would act on it." Decision: FIX — surface the status on the roster, either by banding the card grid in panel_open/panel_close or by rendering the badge and reason above the grid when status.demoted. Cost if wrong: one more badge on a page.

### 87

Task 26: Ruling (Important 2, plan-mandated): `clock.parse(since + "T00:00:00Z")` has no error handling, so `?since=not-a-date` raises ValueError and returns an unhandled 500 — the reviewer confirmed this by running it. It is user-reachable by hand-editing the URL and untested. Decision: FIX — catch ValueError and fall back to the default window. Falling back rather than 400ing suits this product, and it stays honest because the page already renders `view.window_label`, so the user sees which window is actually in use. Cost if wrong: a bad date silently yields the default instead of an error page.

### 88

Task 27: Ruling: raw SQL (`SELECT * FROM unattributed ...`) sits directly in routes/sync.py. In Task 24 I moved the home page's queue SQL into metrics/ for exactly this reason, and the architecture is explicit that "metrics know only SQL, templates know only formatting". Leaving it here would make the boundary hold on three pages and not the fourth. Decision: FIX — move it to metrics/people.py as a small reader; the unattributed table is a list of identities that failed to resolve to a person, so it is roster-adjacent and that is where it belongs. Cost if wrong: one function in a slightly odd module.

### 89

Task 27: Ruling: sync.html's three panels pass no panel_status. ACCEPTED, no fix. Everywhere else a panel_status warns that the data a panel displays may be incomplete — but the sync page's SUBJECT MATTER is the manifest itself. Its panels exist to show that github/issues FAILED. Badging such a panel to say the failure data might be unreliable would be circular and would dilute the badge vocabulary's meaning everywhere else. The implementer's reasoning was right. Cost if wrong: the sync page lacks a badge it never needed.

### 90

Task 27: Ruling: the implementer found a genuine Jinja bug in the brief's base.html snippet — `{% include ... with context ignore missing %}` does not parse; the correct order is `ignore missing with context`. ACCEPTED as a necessary correction; the brief's line simply could not have worked. Task 27: it also ADJUSTED a pre-existing test in test_web_chrome.py (a previously-reviewed task) whose assertion assumed the old ctx.synced-gated /sync behaviour that this task's route replaces. Flagged to the reviewer for close scrutiny — modifying an already-approved test is exactly where a real regression can be laundered into a passing suite. Task 27: the modified test_web_chrome.py assertion was scrutinised and cleared — it is genuine obsolescence, not a laundered regression. Task 23's own brief says the ctx.synced stub gate on all four routes was temporary ("Tasks 24-27 replace each body"), and this task's brief removes that gate from /sync specifically and gives it a page-specific CTA empty state. Home/delivery/people correctly keep the original generic assertion. Reviewer quoted before and after.

### 91

Task 27: Ruling (Important, plan-mandated): the route's lock check is `if lock.exists()` — file existence ONLY — while `SyncLock.__enter__` in sync.py properly checks `_process_alive(pid)` and reclaims a lock whose process is gone, with an actionable message naming the file to delete. So if a sync is killed mid-run (SIGKILL, OOM, power loss), the lock file survives and EVERY later POST from the web UI returns "A sync is already running." forever. The Sync button — the single control this task exists to build — becomes permanently dead with no recovery path visible anywhere in the UI. No test covers it: the one lock test uses a LIVE process lock, which satisfies `exists()` trivially and never exercises the stale branch. Decision: FIX — the route reuses SyncLock's liveness check rather than reimplementing a weaker one. Cost if wrong: none; it strictly widens when the button works.

### 92

Task 27: Ruling: I am also fixing the reviewer's second Minor. The background `task()` catches only WaypointError, so any other exception escapes and progress.json stays at state="running" forever — the UI polls and shows "syncing" indefinitely with no error surfaced. The lock does release, so it is self-healing on retry, but until then the page states something false. Same principle as every other fix this session: never leave the user looking at a confident wrong state. Cost if wrong: an unexpected error surfaces as a failed sync instead of a hung one, which is the honest rendering.

### 93

Task 28: Ruling: the implementer flagged that validate_sidecar checks CONTAINER types (dict/list) but never SCALAR types, so a non-string title/body/severity passes the trust boundary. I initially judged this a crash path — the severity macro does `{{ word|upper }}` and I assumed a dict would raise into a 500. I TESTED it rather than ruling on the assumption, and I was wrong: Jinja's `upper` routes through soft_str, so {'a':1} renders as "{'A': 1}" and nothing raises; autoescape also correctly neutralises `<script>alert(1)</script>` to &lt;script&gt;. So the real exposure is COSMETIC — a confused sidecar renders an ugly repr, not a crash and not an injection. Decision: NO fix round. Adding scalar type-checking would go beyond the brief for a display nit, and the malformed path already exists for genuine structural failures. Recording the near-miss because I nearly opened a fix round on an unverified premise. Cost if wrong: a wrong-typed field renders as its Python repr on the reports page.

### 94

Task 28: Ruling (CRITICAL, plan-mandated): `_load` wraps the sidecar read in `except (json.JSONDecodeError, SidecarError)` only, but `Path.read_text()` raises UnicodeDecodeError on non-UTF-8 bytes, which is not caught. The reviewer PROVED it with a probe: a sidecar containing raw non-UTF-8 bytes makes `all_reports()` and `latest()` raise, taking down the ENTIRE report listing — every other valid report with it. That is strictly worse than the malformed- report case the module was built to handle, and it directly violates the constraint that a malformed sidecar degrades honestly rather than crashing. Decision: FIX — catch the decode failure and route it into the same malformed-report branch. Cost if wrong: none.

### 95

Task 28: Ruling (Important, plan-mandated): in the malformed branch, `skill` is derived by stripping only the date prefix from the filename stem, so a malformed PERSON-SCOPED sidecar yields skill "waypoint:one-on-one-prep-alex-rivera" instead of "waypoint:one-on-one-prep" — the person id stays welded on. The reviewer proved this with a probe too. It matters because I checked Task 29: line 472 renders `{{ report.skill }}` directly on the page, so the mangled id is user-visible, and it matches no real skill id for grouping. Decision: FIX — the malformed branch must not fabricate a skill id it cannot verify. I also answered the reviewer's open question: Task 29 passes person_id INTO `latest(spec.name, person_id=...)` and never reads it back off a Report, so NO new `person_id` field is required. Cost if wrong: a malformed person-scoped report shows a blank or raw-stem label instead of a wrong one — the honest rendering.

### 96

Task 29: Ruling (security, addressed before review): `run_skill` calls subprocess_run with no `env=`, so the `claude` child inherits the FULL parent environment — including WAYPOINT_GITHUB_TOKEN and WAYPOINT_JIRA_TOKEN whenever they are exported as real env vars rather than living only in `.env`. The skill has no legitimate need for them: it reads `.waypoint/` through `waypoint query` and never calls GitHub or Jira itself. This is the one place in the product where Waypoint hands control to an external process, and the binding constraint is that tokens never reach a log, a report, or anything they need not. Decision: FIX — pass an explicit environment that excludes the three WAYPOINT_* secrets. Verbatim from the brief, but the brief never considered it. Cost if wrong: a skill that somehow needed a token would have to be given it deliberately, which is the correct default. Task 29: env-scrub fix landed — commit 63b94b5, 383/383 passing. New test test_the_subprocess_never_receives_waypoint_secrets asserts on the env dict handed to the fake subprocess_run: none of the three WAYPOINT_* secrets present, PATH still is.

### 97

Task 29: Ruling (Important, plan-mandated): routes/home.py:57-83 constructs Risk/Evidence objects and sorts the merged register inline in the route, breaching "web/ renders and never computes" — and it re-literals {"high": 0, "med": 1, "low": 2} instead of importing the identical SEVERITY_ORDER already defined at metrics/risks.py:20 and used at :269. That is a duplicated business rule which will silently drift the moment a severity level is added. This is the THIRD instance of the same boundary breach (Task 24's home queues, Task 27's unattributed query), and I ruled to fix both of those. Decision: FIX, for consistency and because the duplicated constant is a real drift hazard. Move the merge/sort into metrics/risks.py and use the existing SEVERITY_ORDER. Cost if wrong: one function relocated.

### 98

Task 30: Ruling (Important): skills/growth-review/example-output.json is internally inconsistent in exactly the way the grounding rule exists to prevent. Its body claims "Recent window (7 issues total)" while naming 5 keys, and "Prior window (8 issues total)" while naming 7; of the keys named in prose, only the 5 feature ones appear in `evidence` — the bug and toil keys are cited with no evidence entry at all. A second item then claims "a further issue not shown here", contradicting the first item's own enumeration. It passes validate_sidecar and renders, so it is not Critical — but this is the CANONICAL EXAMPLE the SKILL.md tells a model to read before writing its own report, for the one skill whose whole purpose is accurate volume and composition claims. Shipping it teaches precisely the behaviour the product forbids. Decision: FIX. Cost if wrong: an example's numbers change.

### 99

Task 30: Ruling: also fixing the reviewer's Minor in skills/workload-review/example-output.json — body claims three child issues under PROJ-70 but evidences only two. Same class, adjacent file, one line, and it would be odd to ship a corrected example beside an uncorrected one.

### 100

Ruling (final-review finding 1, CRITICAL): `stale_status()` returns OK_STATUS — a TRUTHY dataclass — when a report's digest matches, so home.html's `report_status if report_status else register_status` discards the FAILED/PARTIAL demotion whenever a FRESH report exists. Verified live: same failed manifest renders `panel demoted demoted-failed` + FAILED badge with no report, and a bare `panel` with none of it once a fresh report lands. The register goes on showing risks computed from an index missing every Jira issue with nothing saying so — the exact failure §4 names as the only one causing real harm, on the headline panel. This is MY T13/T24/T29 ruling's fault: I said report_status takes precedence "whenever it is set" to satisfy Task 29's STALE test, and never considered that it is also set — to OK — in the healthy case. Decision: FIX — take the WORSE of the two via a helper in metrics/status.py, not a truthiness test in a template. Cost if wrong: a panel shows STALE where FAILED was worse.

### 101

Ruling (finding 2, Important): a corrupt or half-written state/ file 500s ALL FOUR pages plus the sync page that would have explained it — verified live. ManifestStore.load and read_progress both splat JSON into dataclasses unguarded. Worse, ManifestStore.save writes atomically (.tmp + replace) but write_progress does NOT, so a sync killed mid-write bricks the entire UI with a file Waypoint itself wrote. The reviewer flags my T5 deferred note ("arguably correct, fail loud over silent incompleteness") as MIS-GRADED and it is right: failing loud here means an unhandled traceback on every page, which UI§9 forbids outright. Decision: FIX both loads and make write_progress atomic like its sibling.

### 102

Ruling (finding 3, Important): PageContext.sync_state is computed ("partial"/"failed"/"") and NO template reads it — the partial derives its class from progress.state, which is "idle" on a normal page load. Verified live: a failed manifest renders `<span class="sync-state ">last sync failed</span>` in grey, where UI§5 requires med/high colouring. `.sync-state.partial` in the CSS is unreachable. Task 23 produced the field, Task 27 wrote the consumer without it — invisible to both reviews. Decision: FIX.

### 103

Ruling (finding 4, Important): `github.bot_logins` is read in exactly ONE place (derive.py:83, skipping bot reviews for first-review time). The index's unattributed recorder has no bot filter, so the roster-health panel tells the user "add to github.bot_logins" and doing so changes NOTHING — the identity returns after every sync forever. Verified end-to-end: after a full sync with bot_logins set, ('github','dependabot','author',1) is still in `unattributed`. UI§5 promises this as a suggested fix. Decision: FIX the filter (the hint is correct; the code is not).

### 104

Ruling (finding 5, person page discards skill output): the person route loads one-on-one-prep's report and the partial renders only the SKILL badge, name and generated_at — `report.items`, which ARE the product of the skill, are never rendered. So clicking Analyze spends a Claude invocation to produce output the user can only read by opening the .md on disk. The reviewer notes the two specs disagree: §12 says "the panel re-renders with the narrative", UI§5 Person says only "then the Analyze strip". Decision: NOT fixed in this wave; surfaced to the human as a follow-up. Why: this is an UNDERSPECIFIED FEATURE, not a defect, and choosing between two spec sections on the user's behalf — in a fix wave, as the last action before merge — is exactly the call that should be theirs. Everything else I ruled on had a binding constraint to appeal to; this has two clauses pointing different ways. Cost of leaving it: the person page's Analyze button is half-wired until they decide. Flagged prominently in the handover.

### 105

Ruling (finding 6, Minor -> fixing): metrics/risks.py:21 imports store.reports.ReportItem at RUNTIME. store.reports is not on the metrics/ allow-list and does mkdir/read_text/write_text. Only the pure dataclass is used so nothing touches disk, but metrics/status.py already solves this correctly with a TYPE_CHECKING guard, and this arrived in the LAST refactor commit after the boundary had held 29 times. Decision: FIX — guard it the same way status.py does.

### 106

Ruling (finding 7a, Minor -> fixing): deps.py:53-54 computes `(now - stamp).total_seconds() / 3600` then `hours * 60` to build the sync_label. That is a date difference AND a unit conversion producing a value the user READS, which fails my own stated test from Task 24 (computation producing what the user sees belongs in metrics/; computation selecting what to ask for is routing). Decision: FIX. Finding 7b (people.py:63's `ctx.now - timedelta(days=14)`) I stand by as acceptable under that same test — it produces a query bound, and the window the user sees comes back from metrics as view.window_label.

### 107

Ruling: promoting three of the reviewer's MIS-GRADED deferred minors into the fix wave — (a) T7's unguarded `response.json()`: a non-JSON 200 raises JSONDecodeError, which is not a SourceError, so on the CLI path it escapes run_sync's handler entirely and write_progress never runs, leaving progress.json stuck at "running" forever and the Sync button blocked; (b) T22's `--format table` printing NOTHING on zero rows, not even a header — and `waypoint query` is the ONLY data access the five skills have, so a silent empty response is indistinguishable from "no rows matched" and undermines the very grounding rule the skills rest on; (c) T1's untracked uv.lock — one command, and build reproducibility is a real property to lose.

### 108

Ruling: also folding in the mechanical batch (six dead imports across index.py, risks.py and four test modules, plus T6's `if delay else ""` dropping a legitimate `Retry-After: 0`) — no linter is configured, so nothing else will ever catch these.

### 109

Ruling: adding the END-TO-END PIPELINE TEST the reviewer recommends. It observed that tests/factories.py writes the index directly, so connector->build and build->metric are each asserted while the JOIN between them never is — which is precisely why finding 4 survived 30 task reviews. It wrote one as a probe in ~40 lines. This is the single highest-value test to add and it closes the class. Final review: ONE fix wave dispatched (per process: one fix dispatch, one scoped re-review, then adjudicate residuals — there is no second wave).

### 110

Ruling: the fix agent DECLINED item 8's "remove unused Iterable from store/index.py" and it is right — item 4's own fix in the same wave gives `_Unattributed.__init__` a `bot_logins: Iterable[str]` parameter, so the import is now load-bearing. Removing it would have broken the fix landing beside it. Accepted; my instruction was stale by the time it ran.

### 111

Ruling: it also corrected MY claim in item 7a that a stuck sync would block the Sync button. SyncLock.__exit__ releases the lock as the exception unwinds, so lock_holder_alive is False. The stuck progress.json and the CLI traceback were real and are fixed; my stated consequence was wrong. Accepted. Note (its own disclosure, and the best thing in the report): its FIRST version of the item-1 web test PASSED against the unfixed code — `"FAILED" in body` was satisfied by the board panel next door. It caught its own false positive, added a panel_html() slicer, and only then reproduced the bug. That is exactly the failure mode this project has hit ten times, caught by the agent on itself.

### 112

Ruling (adjudicating the one residual, per process — there is no second fix wave): `import pytest` at tests/test_source_github.py:5 was NOT removed, and the fix report's stated reason for keeping it — that the new non-JSON test uses pytest.raises — is FALSE; that test asserts on source.status(), not on a raised exception. Decision: PARK it. It is an unused import in a test file with no behavioural effect, and the process allows exactly one fix wave. But it must not be recorded as done, and the false rationale must not stand: I am recording it accurately as open and surfacing it to the human. Cost if wrong: one dead import line in a test module. Task/branch state: 454/454 passing. README committed separately as ee6a00a.

### 113

Ruling: Jira's `/rest/api/3/search` was REMOVED by Atlassian and answers 410 (CHANGE-2046), so the connector could not sync at all. Migrated to the enhanced search endpoint, `/rest/api/3/search/jql`. Three things changed and one deliberately did not. (a) Pagination is now an opaque cursor — `nextPageToken` plus `isLast` — and the response no longer carries `total`, so the page loop that terminated on `startAt >= total` is now bounded by the cursor. (b) `expand=changelog` IS still accepted on the new endpoint, which is the finding that kept this small: issues and history still arrive in one request and the `POST /rest/api/3/changelog/bulkfetch` rework Atlassian points migrators at was not needed. (c) The changelog-truncation signal from #52 is untouched, because it reads the `total` embedded in each issue's own `changelog` object, which belongs to the issue resource and not to the search envelope — two different `total` fields that the removal notice makes easy to conflate.

### 114

Ruling: two guards added beyond a like-for-like port, both because the migration removed the bound that made the old loop safe. First, Jira has been reported in the wild to hand back the same `nextPageToken` indefinitely with `isLast` never turning true; with `total` gone there is nothing left to stop that, and an endless loop would spin inside the sync lock with `progress.json` stuck at "running" — the exact failure #2100049 was fixed to prevent. A repeated token now stops the loop. But stopping silently would advance the watermark over issues that were never fetched, which is the silent-incompleteness this connector exists to refuse, so `issues` is reported `partial` with an actionable error. Note the JQL's `ORDER BY updated ASC` makes the newest `updated` seen a valid resume point even on a short page loop, so the cost of bailing out is a re-fetch, never a skipped issue. Second: verification here is fixture-based, not against a live site, and `expand=changelog` support on the new endpoint is documented rather than observed. If it ever stops populating, every issue arrives with NO `changelog` key, which `_note_changelog_truncation` cannot detect (it returns early on a missing `total`) and which would be yielded as `{"histories": []}` — indistinguishable from an issue that genuinely never moved, silently zeroing cycle time, stall detection, item age and historical WIP. A missing `changelog` key is therefore now reported `partial` too. Cost if wrong: two extra PARTIAL paths that should never fire in a healthy sync.

## Known open items

- `tests/test_source_github.py` carries an unused `import pytest`. The final fix wave
  recorded it as removed; it was not, and the stated reason (that a new test uses
  `pytest.raises`) is incorrect — that test asserts on recorded entity status. Cosmetic.
- The person page's Analyze button produces a report whose items are never rendered, so
  its output is readable only by opening the markdown file on disk. The two specs
  disagree about what that strip should show — system design §12 says "the panel
  re-renders with the narrative", UI design §5 says only "then the Analyze strip". This
  is an underspecified feature and was deliberately left for a human to decide.
- Roughly 70 minor findings were triaged as safe to leave: untested edge cases, weak
  assertions, cosmetic naming, and documented consequences of design choices the plan
  made. Most fail toward honesty — over-demoting a panel rather than under-demoting it.
- The Jira migration to `/rest/api/3/search/jql` (#113/#114) is verified against fixtures
  only, by agreement. Two documented behaviours of the new endpoint remain unobserved on a
  live site: that `expand=changelog` genuinely populates, and that `isLast` / `nextPageToken`
  terminate as specified. Both failure modes now surface as PARTIAL rather than as silently
  wrong data, but the first real sync against a Jira site is what actually confirms them.
