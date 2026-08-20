# Installing the Waypoint skills

The five skills ship in `skills/`. Install them into the working directory so they
are invocable as `/waypoint:<name>` in a Claude Code session there, and headlessly by
the dashboard's Analyze buttons — one definition, two entry points.

    mkdir -p .claude/skills
    cp -R skills/* .claude/skills/

Verify: `ls .claude/skills` lists five directories, and `/waypoint:delivery-risk` appears
in a Claude Code session started in this directory.

If Claude Code is not installed, the dashboard still works: Analyze strips are replaced
by a note saying generated analysis is unavailable, and nothing else changes.
