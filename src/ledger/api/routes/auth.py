from __future__ import annotations

from fastapi import APIRouter, Depends

from ledger.api.deps import CurrentUser, SessionDep, SettingsDep, enforce_login_rate_limit
from ledger.api.schemas import LoginRequest, Problem, RegisterRequest, TokenOut, UserOut
from ledger.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(tags=["auth"])


@router.post(
    "/register",
    status_code=201,
    responses={409: {"model": Problem}, 422: {"model": Problem}},
    summary="Create a user",
)
def register(body: RegisterRequest, session: SessionDep, settings: SettingsDep) -> UserOut:
    user = AuthService(session, settings).register(body.email, body.password)
    session.commit()
    return UserOut.from_model(user)


@router.post(
    "/token",
    dependencies=[Depends(enforce_login_rate_limit)],
    responses={401: {"model": Problem}, 429: {"model": Problem}},
    summary="Exchange email and password for an access token",
)
def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenOut:
    auth = AuthService(session, settings)
    user = auth.authenticate(body.email, body.password)
    session.commit()  # persists a password re-hash if the hashing parameters were upgraded
    token = auth.issue_token(user)
    return TokenOut(access_token=token.access_token, expires_in=token.expires_in)


@users_router.get("/me", responses={401: {"model": Problem}}, summary="The current user")
def me(user: CurrentUser) -> UserOut:
    return UserOut.from_model(user)
