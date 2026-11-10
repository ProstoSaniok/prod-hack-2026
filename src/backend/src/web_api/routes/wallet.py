from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from src.application.wallet.entity import LedgerEntry, SourceKind
from src.application.wallet.queries import WalletQueries
from src.web_api.http.routing import WalletRoute


class WalletResponse(BaseModel):
    settled: int = Field(ge=0)
    reserved: int = Field(ge=0)
    available: int = Field(ge=0)
    unit: Literal["PRODиты"] = "PRODиты"


class TransactionKind(StrEnum):
    WELCOME = "welcome"
    CASE_REWARD = "case_reward"
    REVIEW_REWARD = "review_reward"
    RESERVE = "priority_reserve"
    RELEASE = "priority_release"
    SETTLE = "priority_settle"
    REFUND = "priority_refund"


_KINDS = {
    SourceKind.WELCOME: TransactionKind.WELCOME,
    SourceKind.SUCCESSFUL_CASE: TransactionKind.CASE_REWARD,
    SourceKind.REVIEW_REWARD: TransactionKind.REVIEW_REWARD,
    SourceKind.RESERVE: TransactionKind.RESERVE,
    SourceKind.RELEASE: TransactionKind.RELEASE,
    SourceKind.SETTLE: TransactionKind.SETTLE,
    SourceKind.REFUND: TransactionKind.REFUND,
}


class WalletTransactionResponse(BaseModel):
    id: UUID
    sequence: int = Field(ge=1)
    source_id: str
    kind: TransactionKind
    settled_delta: int
    reserved_delta: int
    priority_request_id: UUID | None
    assignment_id: UUID | None
    created_at: datetime

    @classmethod
    def from_entry(cls, entry: LedgerEntry) -> "WalletTransactionResponse":
        source = f"{entry.source.kind.value}:{entry.source.subject_id}"
        if entry.source.scope_id is not None:
            source += f":{entry.source.scope_id}"
        return cls(
            id=entry.id,
            sequence=entry.sequence,
            source_id=source,
            kind=_KINDS[entry.source.kind],
            settled_delta=entry.settled_delta,
            reserved_delta=entry.reserved_delta,
            priority_request_id=entry.reservation_id,
            assignment_id=entry.assignment_id,
            created_at=entry.created_at,
        )


class WalletErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


router = APIRouter(
    route_class=WalletRoute,
    prefix="/api/v1/me/wallet",
    tags=["Развитие"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={
        401: {
            "model": WalletErrorResponse,
            "description": "Missing or invalid opaque bearer token",
            "headers": {"WWW-Authenticate": {"schema": {"type": "string", "enum": ["Bearer"]}}},
        },
        404: {"model": WalletErrorResponse, "description": "Account wallet is not initialized"},
        422: {"model": WalletErrorResponse, "description": "Invalid pagination"},
        503: {"model": WalletErrorResponse, "description": "Authentication or wallet storage unavailable"},
    },
)


@router.get("", operation_id="get_my_wallet")
async def get_my_wallet(queries: FromDishka[WalletQueries]) -> WalletResponse:
    wallet = await queries.get_wallet()
    return WalletResponse(settled=wallet.settled, reserved=wallet.reserved, available=wallet.available)


@router.get("/transactions", operation_id="list_my_wallet_transactions")
async def list_my_wallet_transactions(
    queries: FromDishka[WalletQueries],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[WalletTransactionResponse]:
    entries = await queries.list_entries(limit=limit, offset=offset)
    return [WalletTransactionResponse.from_entry(entry) for entry in entries]
