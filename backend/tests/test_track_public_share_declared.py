from app.services.track_public_share import _track_manifest_spec_match


def test_track_manifest_spec_match_by_key():
    spec = {"key": "employee_onboarding", "name": "Employee Onboarding"}
    assert _track_manifest_spec_match(
        spec, template_id="employee_onboarding", title_slug=""
    )


def test_track_manifest_spec_match_by_title():
    spec = {"key": "employee_onboarding", "name": "Employee Onboarding"}
    assert _track_manifest_spec_match(
        spec, template_id="", title_slug="employee_onboarding"
    )
