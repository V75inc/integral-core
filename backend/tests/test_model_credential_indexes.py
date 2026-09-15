"""Index contract for UserModelCredential on shared object collection."""

from app.models.credentials import UserModelCredential


def test_user_model_credential_unique_index_scoped_to_entity():
    """Unique user_id index must not collide with RefreshToken rows."""
    indexes = UserModelCredential.get_indexes()
    unique_user = [
        idx
        for idx in indexes
        if idx.get("unique")
        and (
            idx.get("field") == "context.user_id"
            or any(field == "context.user_id" for field, _ in idx.get("fields", []))
        )
    ]
    assert len(unique_user) == 1
    pfe = unique_user[0].get("partialFilterExpression") or {}
    assert pfe.get("entity") == "UserModelCredential"
    assert pfe.get("context.user_id") == {"$gt": ""}
