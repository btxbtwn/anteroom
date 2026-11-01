"""Moving this Mac's listings into the seller's account (the Mac app only)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from vendoo_studio.services import account_move

router = APIRouter(prefix="/api/account-move", tags=["account-move"])


class StartMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    # Used once to sign in to the account; never stored or logged.
    password: str = Field(min_length=1, max_length=1024)


@router.get("")
def move_status():
    return account_move.status()


@router.post("", status_code=202)
def start_move(body: StartMove):
    if not account_move.start(body.email.strip(), body.password):
        raise HTTPException(409, "This Mac is already moving to your account.")
    return account_move.status()
