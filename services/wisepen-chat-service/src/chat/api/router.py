from chat.api.endpoints import chat, chat_file, memory, model, search_provider, session
from fastapi import APIRouter

api_router = APIRouter()

api_router.include_router(chat.router, prefix="", tags=["chat"])
api_router.include_router(chat_file.router, prefix="/file", tags=["file"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(model.router, prefix="/model", tags=["model"])
api_router.include_router(
    search_provider.router,
    prefix="/searchProvider",
    tags=["searchProvider"],
)
