"""A planned seed must copy the approved field map. Blanks are not values."""

from app.services.design_blueprint import plan_fidelity_errors

_BLUEPRINT = {
    "app": {"name": "Car Rental Desk"},
    "tracks": [
        {
            "id": "track.vehicles",
            "name": "Vehicles",
            "entry_types": [
                {
                    "key": "vehicle",
                    "name": "Vehicle",
                    "fields": [
                        {"key": "make", "name": "Make", "type": "text"},
                        {"key": "color", "name": "Color", "type": "text"},
                    ],
                }
            ],
        }
    ],
    "seeds": [
        {
            "id": "seed.civic",
            "track": "track.vehicles",
            "title": "Honda Civic",
            "fields": {"make": "Honda"},
        }
    ],
}


def _operations(fields):
    return [
        ("integral_create_app", {"name": "Car Rental Desk"}),
        (
            "integral_create_app_track",
            {
                "name": "Vehicles",
                "entry_types": [
                    {
                        "name": "Vehicle",
                        "fields": [
                            {"key": "make", "name": "Make", "type": "text"},
                            {"key": "color", "name": "Color", "type": "text"},
                        ],
                    }
                ],
            },
        ),
        (
            "integral_create_entry",
            {
                "track_id": "{{track.id:Vehicles}}",
                "title": "Honda Civic",
                "fields": fields,
            },
        ),
    ]


def _seed_errors(fields):
    errors = plan_fidelity_errors(_BLUEPRINT, _operations(fields), new_app=True)
    return [error for error in errors if error.startswith("Seed ")]


def test_blank_extra_keys_do_not_fail_a_matching_seed():
    assert _seed_errors({"make": "Honda", "color": "", "notes": None}) == []


def test_missing_approved_value_is_named():
    message = " ".join(_seed_errors({}))
    assert "Missing: make." in message
    assert "Copy the approved seed fields exactly." in message
    assert "blank" not in message.casefold()
    assert "required" not in message.casefold()


def test_real_extra_and_changed_value_are_named():
    message = " ".join(_seed_errors({"make": "Toyota", "color": "red"}))
    assert "Extra: color." in message
    assert "Mismatched: make (approved 'Honda')." in message


def test_board_is_the_kanban_view_and_a_rewritten_title_still_fails():
    blueprint = {
        **_BLUEPRINT,
        "views": [
            {
                "id": "view.board",
                "track": "track.vehicles",
                "name": "Rentals Board",
                "type": "kanban",
            }
        ],
    }
    operations = _operations({"make": "Honda"}) + [
        (
            "integral_save_view",
            {
                "track_id": "{{track.id:Vehicles}}",
                "name": "rentals board",
                "view_type": "board",
            },
        ),
        (
            "integral_create_entry",
            {
                "track_id": "{{track.id:Vehicles}}",
                "title": "jane doe rents toyota corolla",
                "fields": {"make": "Honda"},
            },
        ),
    ]
    errors = plan_fidelity_errors(blueprint, operations, new_app=True)
    assert not any("omits approved" in error and "view" in error for error in errors)
    assert any("jane doe rents toyota corolla" in error for error in errors)
    assert not any("omits approved seed 'Honda Civic'" in error for error in errors)
