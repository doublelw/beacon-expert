"""Beacon专家 - 全局配置."""
import os
import shutil
import platform
from pathlib import Path

# === 路径 ===
BASE_DIR = Path(__file__).resolve().parents[1]  # beacon-expert/
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
TASKS_DIR = STORAGE_DIR / "tasks"
FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
FONT_DIR = BASE_DIR / "fonts"
CONFIG_FILE = DATA_DIR / "config.json"

# === 数据库 ===
DB_PATH = DATA_DIR / "beacon.db"
DB_URL = f"sqlite:///{DB_PATH}"

# === FreeCAD ===
def _find_freecadcmd():
    """跨平台探测 freecadcmd."""
    p = shutil.which("freecadcmd")
    if p:
        return p
    env = os.getenv("FREECAD_BIN")
    if env and os.path.isfile(env):
        return env
    defaults = {
        "Darwin": "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
        "Linux": "/usr/bin/freecadcmd",
    }
    d = defaults.get(platform.system())
    if d and os.path.isfile(d):
        return d
    return "freecadcmd"  # fallback

FC_BIN = _find_freecadcmd()

# === saas 引擎源 ===
SAAS_CORE = Path("/Users/ahs/project/Beacon/saas/core")
SAAS_OUTPUT = Path("/Users/ahs/project/Beacon/saas/output")

# === 上传限制 ===
MAX_UPLOAD_MB = 50
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_SUFFIX = {".stp", ".step"}

# === CORS ===
CORS_ORIGINS = ["http://localhost:8766", "http://localhost:8767", "http://127.0.0.1:8767"]

# === JWT ===
JWT_SECRET = os.getenv("BEACON_JWT_SECRET", "beacon-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
JWT_REFRESH_EXPIRE_DAYS = 7

# === LLM ===
LLM_PROVIDERS = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "models": ["glm-5-turbo", "glm-4.5-air"],
        "protocol": "anthropic",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5-20250514"],
        "protocol": "anthropic",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o"],
        "protocol": "openai",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat"],
        "protocol": "openai",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "models": ["llama3"],
        "protocol": "openai",
    },
}

# === 初始化目录 ===
for d in [DATA_DIR, STORAGE_DIR, TASKS_DIR, FONT_DIR]:
    d.mkdir(parents=True, exist_ok=True)
