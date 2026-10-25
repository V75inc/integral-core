"""Bounded generated image evidence shared by search and direct agent reads."""

from app.models.nodes import Attachment


def image_description_text(attachment: Attachment) -> str:
    """Return labeled multimodal observations for these exact current bytes."""
    if not attachment.mime_type.startswith(
        "image/"
    ) or attachment.metadata_status not in {"complete", "partial"}:
        return ""
    semantic = (attachment.metadata or {}).get("semantic", {})
    if (
        not isinstance(semantic, dict)
        or semantic.get("source_hash") != attachment.content_hash
    ):
        return ""
    description = semantic.get("description")
    if not isinstance(description, str) or not description.strip():
        return ""
    text = "Generated image description: " + description[:2000]
    visible = semantic.get("visible_text")
    if isinstance(visible, str) and visible.strip():
        text += "\nModel-read visible text: " + visible[:4000]
    keywords = semantic.get("keywords")
    if isinstance(keywords, list):
        text += "\nKeywords: " + "; ".join(
            word[:128] for word in keywords[:16] if isinstance(word, str)
        )
    return text
