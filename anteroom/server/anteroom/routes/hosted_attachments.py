"""Authenticated reads of private, conversation-owned chat images."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from anteroom.repositories.queries import ConversationRepo
from anteroom.services.chat_attachments import attachment_path, attachment_thumbnail
from anteroom.services.hosted_database import get_seller_db
from anteroom.services.hosted_storage import checked_photo_key
from anteroom.services.photos import THUMBNAIL_SIZES

router = APIRouter()


@router.get('/api/chat-attachments/{attachment_id:path}')
def attachment(attachment_id: str, size: int | None = None, db=Depends(get_seller_db, scope="function")):
    from anteroom.models.assistant import AssistantThread

    checked_photo_key(attachment_id, db.user_id)
    # Images belong to an owned listing chat or an owned assistant conversation.
    collection = attachment_id.split('/')[1]
    if ConversationRepo(db).get(collection) is None and db.get(AssistantThread, collection) is None:
        raise HTTPException(404, 'Attachment not found')
    db.rollback()
    if size is not None and size not in THUMBNAIL_SIZES:
        raise HTTPException(400, 'Unsupported thumbnail size')
    path = attachment_path(attachment_id)
    assert path is not None  # The hosted photo store resolves or rejects the key.
    if size is not None:
        path = attachment_thumbnail(path, size)
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
