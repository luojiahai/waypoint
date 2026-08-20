---
name: waypoint:delivery-risk
description: Rank what is at risk of not landing, with evidence from the indexed GitHub and Jira data, and a suggested next move for each item.
---

# Delivery risk

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

Rank what is most likely to not land, worst first. For each item state what is wrong,
why it matters, the evidence, and one concrete next move.

Waypoint already computes rule-derived risks — an unreviewed PR, a stalled issue, a
column over its limit. Do not restate them. Your value is the reading across items that
a rule cannot make: two PRs blocked on the same unavailable reviewer, an epic whose
remaining work is all in one unstarted issue, a pattern of rework in the changelog.

## Grounding rule

Every claim must cite a specific pull request or issue by its key or number. If the data
does not support a claim, write "insufficient data" instead of inferring. Plausible
narrative that is not in the numbers is the failure mode this rule exists to prevent.

An item with an empty `evidence` array is invalid and will be dropped before it is
rendered, so an item without evidence is simply lost work.

## Output

Write two files into `.waypoint/reports/`, both named `YYYY-MM-DD-delivery-risk`:

1. `YYYY-MM-DD-delivery-risk.md` — the report for a human reader and for git.
2. `YYYY-MM-DD-delivery-risk.json` — the sidecar the dashboard renders.

The sidecar must match this shape exactly:

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
      "body": "PR #482 has been open 5 days with no review; the only other person who has touched this area is on leave.",
      "evidence": [{"type": "pull_request", "ref": "PR #482", "url": "https://…"}],
      "question": "Who can back up Alex on the checkout rework this week?"
    }
  ]
}
```

`severity` is one of `high`, `med`, `low`. Take `inputs_digest` from the `digest` field of
`.waypoint/state/manifest.json`. See `example-output.json` beside this file.
