"""Ownership shared by seller data, on the desktop and hosted databases."""

from sqlalchemy import Column, String

# A reserved owner for the single-seller desktop database. Importing a Mac into
# a cloud account must explicitly map this owner to the verified Supabase UUID;
# signing in alone must never claim somebody else's unclaimed records.
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000000"


class SellerOwned:
    user_id = Column(
        String(36), nullable=False, index=True,
        default=LOCAL_USER_ID, server_default=LOCAL_USER_ID,
    )
