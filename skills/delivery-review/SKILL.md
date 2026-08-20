---
name: waypoint:delivery-review
description: Summarize what is close to landing, what is aging, and where flow is blocked, from the indexed GitHub and Jira data.
---

# Delivery review

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

Three groups, in this order:

1. **Close to landing** — approved but unmerged pull requests, and issues sitting in the
   last column before Done. Say what the remaining step is.
2. **Aging** — in-flight items past the aging threshold, oldest first. Say how long, and
   what the item is waiting on.
3. **Blocked** — issues with no status transition and no linked pull-request activity.
   Say when they last moved.

One sentence per item, ordered within each group by how soon a decision is needed.

## Grounding rule

Every claim must cite a specific pull request or issue by its key or number. If the data
does not support a claim, write "insufficient data" instead of inferring. Plausible
narrative that is not in the numbers is the failure mode this rule exists to prevent.

An item with an empty `evidence` array is invalid and will be dropped before it is
rendered, so an item without evidence is simply lost work.

## Output

Write two files into `.waypoint/reports/`, both named `YYYY-MM-DD-delivery-review`:

1. `YYYY-MM-DD-delivery-review.md` — the report for a human reader and for git.
2. `YYYY-MM-DD-delivery-review.json` — the sidecar the dashboard renders.

The sidecar must match this shape exactly:

```json
{
  "skill": "waypoint:delivery-review",
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
