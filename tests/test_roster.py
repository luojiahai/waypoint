from pathlib import Path

from waypoint.config import load_config
from waypoint.roster import UNATTRIBUTED, Roster


def test_person_ids_are_stable_slugs(waypoint_root: Path):
    roster = Roster.from_config(load_config(waypoint_root))
    assert [p.id for p in roster.people] == ["alex-rivera", "bo-chen"]


def test_resolves_github_login_case_insensitively(waypoint_root: Path):
    roster = Roster.from_config(load_config(waypoint_root))
    assert roster.resolve_github("ARivera") == "alex-rivera"


def test_resolves_jira_account_id(waypoint_root: Path):
    roster = Roster.from_config(load_config(waypoint_root))
    assert roster.resolve_jira("acct-bo") == "bo-chen"


def test_unknown_identities_go_to_the_unattributed_bucket(waypoint_root: Path):
    roster = Roster.from_config(load_config(waypoint_root))
    assert roster.resolve_github("stranger") == UNATTRIBUTED
    assert roster.resolve_jira("acct-nobody") == UNATTRIBUTED
    assert roster.resolve_github(None) == UNATTRIBUTED
    assert roster.resolve_jira("") == UNATTRIBUTED


def test_duplicate_names_get_distinct_ids(waypoint_root: Path):
    text = (waypoint_root / "config.toml").read_text()
    (waypoint_root / "config.toml").write_text(
        text + '\n[[people]]\nname = "Alex Rivera"\ngithub_login = "arivera2"\n'
        'jira_account_id = "acct-alex2"\nactive = true\n'
    )
    roster = Roster.from_config(load_config(waypoint_root))
    assert [p.id for p in roster.people] == ["alex-rivera", "bo-chen", "alex-rivera-2"]


def test_active_people_are_alphabetical_and_exclude_inactive(waypoint_root: Path):
    text = (waypoint_root / "config.toml").read_text().replace(
        'name = "Bo Chen"\ngithub_login = "bchen"\njira_account_id = "acct-bo"\nactive = true',
        'name = "Bo Chen"\ngithub_login = "bchen"\njira_account_id = "acct-bo"\nactive = false',
    )
    (waypoint_root / "config.toml").write_text(text)
    roster = Roster.from_config(load_config(waypoint_root))
    assert [p.name for p in roster.active_people()] == ["Alex Rivera"]


def test_by_id_returns_none_for_the_unattributed_bucket(waypoint_root: Path):
    roster = Roster.from_config(load_config(waypoint_root))
    assert roster.by_id(UNATTRIBUTED) is None
    assert roster.by_id("alex-rivera").name == "Alex Rivera"
