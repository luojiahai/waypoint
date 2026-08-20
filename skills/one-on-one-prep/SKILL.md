---
name: waypoint:one-on-one-prep
description: Prepare for a 1:1 with one person by surfacing what to ask about, never by assessing them.
---

# One-on-one prep

You are analysing one engineering team's delivery data, already fetched and indexed
by Waypoint in the current directory. Do not call GitHub or Jira.

## Reading the data

Query the index through the shipped command, never by invoking sqlite yourself:

    waypoint query "SELECT key, summary, status, status_category FROM jira_issues WHERE status_category = 'In Progress'"
    waypoint query "SELECT id, title, state, url FROM pull_requests WHERE state = 'OPEN'"
    waypoint query "SELECT * FROM pr_flow WHERE review_wait_current IS NOT NULL"
    waypoint query "SELECT * FROM issue_flow WHERE first_done_at IS NULL"

Useful tables: `pull_requests`, `pr_reviews`, `pr_review_requests`, `pr_flow`,
`jira_issues`, `issue_transitions`, `issue_flow`, `issue_pr_links`, `board_columns`,
`people`. Durations in `pr_flow` and `issue_flow.cycle_time` are hours.

When you need the text of a pull request description or an issue comment, read the raw
payloads under `.waypoint/raw/` — they are JSONL, one record per line. That is usually
where the signal about *why* something is stuck actually is.

## What to produce

This skill takes a person id as its argument. Resolve it in the `people` table:

    waypoint query "SELECT * FROM people WHERE id = 'alex-rivera'"

Then surface what to ask this person about: what they shipped in the window, what has been
in flight a long time, what they are waiting on, and who is waiting on them. Each item is
an opening for a conversation, not a finding.

## Constraints

Emit questions, never assessments. Never compare people. Never characterize performance.
Every item's `question` field is required and must be answerable only by the person, not
by the data.

## Grounding rule

Every claim must cite a specific pull request or issue by its key or number. If the data
does not support a claim, write "insufficient data" instead of inferring. Plausible
narrative that is not in the numbers is the failure mode this rule exists to prevent.

An item with an empty `evidence` array is invalid and will be dropped before it is
rendered, so an item without evidence is simply lost work.

## Output

Write two files into `.waypoint/reports/`, both named `YYYY-MM-DD-one-on-one-prep-<person-id>`:

1. `YYYY-MM-DD-one-on-one-prep-<person-id>.md` — the report for a human reader and for git.
2. `YYYY-MM-DD-one-on-one-prep-<person-id>.json` — the sidecar the dashboard renders.

The sidecar must match this shape exactly:

```json
{
  "skill": "waypoint:one-on-one-prep",
  "generated_at": "2026-08-19T09:40:00Z",
  "window": {"from": "2026-08-05", "to": "2026-08-19"},
  "inputs_digest": "sha256:…",
  "items": [
    {
      "severity": "high",
      "title": "Checkout rework has no reviewer coverage",
      "body": "PR #482 has been open 5 days with no review; the only other person who has touched this area is on leave.",
      "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://…"}],
      "question": "Who can back up Alex on the checkout rework this week?"
    }
  ]
}
```

`severity` is one of `high`, `med`, `low`. Take `inputs_digest` from the `digest` field of
`.waypoint/state/manifest.json`. See `example-output.json` beside this file.
