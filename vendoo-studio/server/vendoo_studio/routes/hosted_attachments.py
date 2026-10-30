"""Authenticated reads of private, conversation-owned chat images."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services.chat_attachments import attachment_path, attachment_thumbnail
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.hosted_storage import checked_photo_key
from vendoo_studio.services.photos import THUMBNAIL_SIZES

router = APIRouter()


@router.get('/api/chat-attachments/{attachment_id:path}')
def attachment(attachment_id: str, size: int | None = None, db=Depends(get_seller_db)):
    checked_photo_key(attachment_id, db.user_id)
    if ConversationRepo(db).get(attachment_id.split('/')[1]) is None:
        raise HTTPException(404, 'Attachment not found')
    db.rollback()
    if size is not None and size not in THUMBNAIL_SIZES:
        raise HTTPException(400, 'Unsupported thumbnail size')
    path = attachment_path(attachment_id)
    if size is not None:
        path = attachment_thumbnail(path, size)
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
