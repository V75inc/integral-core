"""The verify result carries the sentence the resident must say."""

from app.services.build_verification import verification_reply


def test_partial_reply_names_what_is_not_present():
    reply = verification_reply(
        {
            "status": "partial",
            "items": [
                {"id": "rentals.status", "status": "mismatch"},
                {"id": "vehicles.plate", "status": "present"},
            ],
        }
    )
    assert reply.startswith("Partial.")
    assert "rentals.status (mismatch)" in reply
    assert "vehicles.plate" not in reply


def test_verified_reply_does_not_say_partial():
    reply = verification_reply({"status": "verified", "items": []})
    assert reply.startswith("Verified.")
    assert "Partial" not in reply
