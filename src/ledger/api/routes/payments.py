"""Money-moving endpoints. Every one requires an Idempotency-Key and is rate limited."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ledger.api.deps import (
    CurrentUser,
    IdempotencyKeyDep,
    SessionDep,
    SettingsDep,
    enforce_user_rate_limit,
)
from ledger.api.schemas import (
    DepositRequest,
    Problem,
    TransactionOut,
    TransferRequest,
    WithdrawalRequest,
)
from ledger.db.models import LedgerTransaction, User
from ledger.observability.metrics import IDEMPOTENT_REPLAYS, LEDGER_TRANSACTIONS
from ledger.services.accounts import AccountService
from ledger.services.idempotency import execute_idempotently, fingerprint_request
from ledger.services.payments import PaymentService

router = APIRouter(tags=["money movement"], responses={401: {"model": Problem}})

_MONEY_MOVEMENT_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": Problem, "description": "Missing or malformed Idempotency-Key"},
    404: {"model": Problem},
    422: {"model": Problem, "description": "Business rule violated, e.g. insufficient funds"},
    429: {"model": Problem},
}


def _move_money(
    request: Request,
    session: Session,
    user: User,
    idempotency_key: str,
    body: BaseModel,
    action: Callable[[], LedgerTransaction],
) -> JSONResponse:
    def operation() -> tuple[int, dict[str, object]]:
        transaction = action()
        return 201, TransactionOut.from_model(transaction).model_dump(mode="json")

    result = execute_idempotently(
        session,
        user_id=user.id,
        key=idempotency_key,
        fingerprint=fingerprint_request(
            request.method, request.url.path, body.model_dump(mode="json")
        ),
        operation=operation,
    )
    headers = {"Location": f"/v1/transactions/{result.body['id']}"}
    if result.replayed:
        IDEMPOTENT_REPLAYS.inc()
        headers["Idempotent-Replayed"] = "true"
    else:
        LEDGER_TRANSACTIONS.labels(str(result.body["kind"])).inc()
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@router.post(
    "/deposits",
    status_code=201,
    response_model=TransactionOut,
    responses=_MONEY_MOVEMENT_RESPONSES,
    dependencies=[Depends(enforce_user_rate_limit)],
    summary="Add money to a wallet from outside the platform",
)
def deposit(
    body: DepositRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKeyDep,
) -> JSONResponse:
    payments = PaymentService(session, fee_bps=settings.transfer_fee_bps)
    return _move_money(
        request,
        session,
        user,
        idempotency_key,
        body,
        lambda: payments.deposit(
            user_id=user.id,
            account_id=body.account_id,
            amount_minor=body.amount,
            description=body.description,
        ),
    )


@router.post(
    "/withdrawals",
    status_code=201,
    response_model=TransactionOut,
    responses=_MONEY_MOVEMENT_RESPONSES,
    dependencies=[Depends(enforce_user_rate_limit)],
    summary="Send money from a wallet out of the platform",
)
def withdraw(
    body: WithdrawalRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKeyDep,
) -> JSONResponse:
    payments = PaymentService(session, fee_bps=settings.transfer_fee_bps)
    return _move_money(
        request,
        session,
        user,
        idempotency_key,
        body,
        lambda: payments.withdraw(
            user_id=user.id,
            account_id=body.account_id,
            amount_minor=body.amount,
            description=body.description,
        ),
    )


@router.post(
    "/transfers",
    status_code=201,
    response_model=TransactionOut,
    responses=_MONEY_MOVEMENT_RESPONSES,
    dependencies=[Depends(enforce_user_rate_limit)],
    summary="Move money from one of my wallets to any wallet",
)
def transfer(
    body: TransferRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKeyDep,
) -> JSONResponse:
    payments = PaymentService(session, fee_bps=settings.transfer_fee_bps)
    return _move_money(
        request,
        session,
        user,
        idempotency_key,
        body,
        lambda: payments.transfer(
            user_id=user.id,
            source_id=body.from_account_id,
            destination_id=body.to_account_id,
            amount_minor=body.amount,
            description=body.description,
        ),
    )


@router.get(
    "/transactions/{transaction_id}",
    responses={404: {"model": Problem}},
    summary="Get a transaction that touched one of my wallets",
)
def get_transaction(
    transaction_id: UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> TransactionOut:
    service = AccountService(session, settings.supported_currencies)
    return TransactionOut.from_model(
        service.get_transaction(owner_id=user.id, transaction_id=transaction_id)
    )
