from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from ledger.api.deps import CurrentUser, SessionDep, SettingsDep
from ledger.api.schemas import AccountCreate, AccountOut, Problem, StatementLineOut, StatementOut
from ledger.services.accounts import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"], responses={401: {"model": Problem}})


@router.post("", status_code=201, responses={422: {"model": Problem}}, summary="Open a wallet")
def open_account(
    body: AccountCreate,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> AccountOut:
    service = AccountService(session, settings.supported_currencies)
    account = service.open_account(owner_id=user.id, name=body.name, currency=body.currency)
    session.commit()
    response.headers["Location"] = f"/v1/accounts/{account.id}"
    return AccountOut.from_model(account)


@router.get("", summary="List my wallets")
def list_accounts(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[AccountOut]:
    service = AccountService(session, settings.supported_currencies)
    return [AccountOut.from_model(a) for a in service.list_accounts(user.id)]


@router.get(
    "/{account_id}", responses={404: {"model": Problem}}, summary="Get a wallet and its balance"
)
def get_account(
    account_id: UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> AccountOut:
    service = AccountService(session, settings.supported_currencies)
    return AccountOut.from_model(service.get_owned_account(owner_id=user.id, account_id=account_id))


@router.get(
    "/{account_id}/statement",
    responses={400: {"model": Problem}, 404: {"model": Problem}},
    summary="Paginated history of money in and out, newest first",
)
def statement(
    account_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> StatementOut:
    service = AccountService(session, settings.supported_currencies)
    page = service.statement(owner_id=user.id, account_id=account_id, limit=limit, cursor=cursor)
    return StatementOut(
        account_id=account_id,
        items=[
            StatementLineOut(
                posting_id=posting.id,
                transaction_id=transaction.id,
                kind=transaction.kind,
                description=transaction.description,
                amount=posting.amount_minor,
                currency=posting.currency,
                created_at=posting.created_at,
            )
            for posting, transaction in page.lines
        ],
        next_cursor=page.next_cursor,
    )
