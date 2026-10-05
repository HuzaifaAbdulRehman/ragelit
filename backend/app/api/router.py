from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.audit_jobs.api import router as audit_router
from app.chat.api import router as chat_router
from app.documents.api import router as documents_router
from app.identity.api import router as auth_router
from app.tenancy.api import router as tenancy_router

router = APIRouter()
router.include_router(health_router)

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(audit_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(chat_router)
api_v1_router.include_router(tenancy_router)
router.include_router(api_v1_router)
