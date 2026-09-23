from techcamp.identity.domain.models import Role


def test_role_matches_the_documented_membership_roles() -> None:
    assert {r.value for r in Role} == {"owner", "technician", "producer", "viewer"}
