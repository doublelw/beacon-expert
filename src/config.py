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
    # 1) PATH
    p = shutil.which("freecadcmd")
    if p:
        return p
    # 2) 环境变量
    env = os.getenv("FREECAD_BIN")
    if env and os.path.isfile(env):
        return env
    # 3) Windows 用户安装版 (Winget/Chocolatey 默认路径)
    if platform.system() == "Windows":
        local_prog = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
        if local_prog.exists():
            for d in local_prog.iterdir():
                if d.is_dir() and "freecad" in d.name.lower():
                    exe = d / "bin" / "freecadcmd.exe"
                    if exe.exists():
                        return str(exe)
        # 系统安装版
        for base in [r"C:\Program Files", r"C:\Program Files (x86)"]:
            bp = Path(base)
            if bp.exists():
                for d in bp.iterdir():
                    if d.is_dir() and "freecad" in d.name.lower():
                        exe = d / "bin" / "freecadcmd.exe"
                        if exe.exists():
                            return str(exe)
    # 4) macOS
    mac = Path("/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")
    if mac.exists():
        return str(mac)
    # 5) Linux
    linux = Path("/usr/bin/freecadcmd")
    if linux.exists():
        return str(linux)
    return "freecadcmd"  # fallback — 期望在 PATH 中

FC_BIN = _find_freecadcmd()

# === saas 引擎源 (本地 beacon-expert 的 core 脚本) ===
SAAS_CORE = BASE_DIR / "src" / "engine"
SAAS_OUTPUT = BASE_DIR / "output"

# === GB 机械制图国标知识 skill (LLM 上下文来源) ===
GB_SKILL_DIR = Path.home() / ".claude" / "skills" / "gb-mechanical-drawing"

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
