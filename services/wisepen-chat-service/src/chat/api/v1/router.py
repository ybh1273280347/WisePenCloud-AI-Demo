from fastapi import APIRouter
from chat.api.v1.endpoints import chat, session, memory

api_router = APIRouter()

# 把 chat 模块的路由包含进来
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(session.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(memory.router, prefix="/memories", tags=["memories"])
