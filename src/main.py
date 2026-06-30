"""Beacon专家 - FastAPI应用入口."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from src.config import CORS_ORIGINS
from src.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Beacon专家",
    description="企业级AI驱动3D→2D工程图自动转换平台",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """健康检查: 服务状态 + FreeCAD可用性 + 磁盘."""
    import shutil, os
    from src.config import FC_BIN, TASKS_DIR
    fc_ok = shutil.which("freecadcmd") is not None or os.path.isfile(FC_BIN)
    disk_free = shutil.disk_usage("/").free // (1024 * 1024)  # MB
    return {
        "status": "ok",
        "freecadcmd": {"path": FC_BIN, "available": fc_ok},
        "disk_free_mb": disk_free,
        "tasks_dir": str(TASKS_DIR),
    }


@app.get("/")
async def root():
    return {"name": "Beacon专家", "version": "0.1.0", "docs": "/docs"}


# === 路由注册 ===
from src.routes import users, knowledge, settings, drawings, memory, convert
app.include_router(users.router)
app.include_router(knowledge.router)
app.include_router(settings.router)
app.include_router(drawings.router)
app.include_router(memory.router)
app.include_router(convert.router)


@app.get("/app")
async def frontend():
    """前端SPA."""
    from src.frontend import HTML
    return HTMLResponse(HTML)
