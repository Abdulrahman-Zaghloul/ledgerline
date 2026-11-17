from fastapi import APIRouter

from ledger.api.routes import accounts, auth, payments

api_v1 = APIRouter(prefix="/v1")
api_v1.include_router(auth.router)
api_v1.include_router(auth.users_router)
api_v1.include_router(accounts.router)
api_v1.include_router(payments.router)
