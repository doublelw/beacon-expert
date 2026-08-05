"""Beacon专家 - SQLite 数据库模型."""
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, Float,
    DateTime, ForeignKey, JSON, Enum as SAEnum, Text, event, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.config import DB_URL, DB_PATH

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, conn_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns():
    """幂等列迁移: 对已存在的旧表用 ALTER TABLE ADD COLUMN 补 project_id.

    create_all 只建新表不改旧表列, 故需要这个补丁把 project_id 加到
    drawings/conversations/conversion_tasks 上 (SQLite 兼容)。
    """
    insp = inspect(engine)
    additions = [
        ("drawings", "project_id", "INTEGER REFERENCES projects(id)"),
        ("conversations", "project_id", "INTEGER REFERENCES projects(id)"),
        ("conversion_tasks", "project_id", "INTEGER REFERENCES projects(id)"),
    ]
    with engine.begin() as conn:
        for table, col, typedef in additions:
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))


def init_db():
    """创建所有表 + 对旧表补 project_id 列."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


# === 用户系统 ===

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="engineer")  # admin/dept_manager/engineer/viewer
    dept_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    department = relationship("Department", back_populates="users")


class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    users = relationship("User", back_populates="department")


# === 项目系统 (多零件按项目组织; 每项目指定 work_dir) ===

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scope = Column(String(20), nullable=False, default="enterprise")  # personal/dept/enterprise
    dept_id = Column(Integer, ForeignKey("departments.id"), nullable=True)  # scope=dept 时所属部门
    work_dir = Column(String(512), nullable=False)  # 用户指定的项目工作目录绝对路径
    tech_reqs = Column(JSON, default=lambda: {  # 项目级技术要求 (适用于全项目零件)
        "material": None, "tolerance": None, "volume": None,
        "priority": None, "special": None,
    })
    status = Column(String(20), nullable=False, default="active")  # active/archived
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# === 知识库 ===

class Knowledge(Base):
    __tablename__ = "knowledge"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scope = Column(String(20), nullable=False)  # personal/dept/enterprise
    dept_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    category = Column(String(100), default="general")
    title = Column(String(255), nullable=False)
    content = Column(Text, default="")
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# === 转换任务 ===

class ConversionTask(Base):
    __tablename__ = "conversion_tasks"
    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    stp_path = Column(String(512), nullable=False)
    dxf_path = Column(String(512), nullable=True)
    status = Column(String(20), nullable=False, default="queued")
    # queued/classifying/projecting/rendering/auditing/done/failed/timeout/degraded
    work_dir = Column(String(512), nullable=False)
    process = Column(String(30), nullable=True)  # 工艺分类结果
    error = Column(Text, nullable=True)
    degraded = Column(Boolean, default=False)
    llm_cost = Column(Float, default=0.0)
    locked_at = Column(DateTime, nullable=True)  # 崩溃恢复: 锁定时间
    heartbeat = Column(DateTime, nullable=True)  # 心跳
    # 中间产物路径(work_dir内)
    veritas_path = Column(String(512), nullable=True)
    proj_path = Column(String(512), nullable=True)
    plan_path = Column(String(512), nullable=True)
    verify_path = Column(String(512), nullable=True)
    eval_path = Column(String(512), nullable=True)
    audit_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


# === 图纸效率系统 ===

class Drawing(Base):
    __tablename__ = "drawings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    name = Column(String(255), nullable=False)
    task_id = Column(String(36), ForeignKey("conversion_tasks.id"), nullable=True)
    step_path = Column(String(512), nullable=False)
    dxf_path = Column(String(512), nullable=True)
    process = Column(String(30), nullable=True)
    bbox = Column(JSON, nullable=True)
    features_summary = Column(JSON, nullable=True)
    tags = Column(JSON, default=list)
    category = Column(String(100), default="general")
    visibility = Column(String(20), default="personal")  # personal/dept/enterprise
    parent_id = Column(Integer, ForeignKey("drawings.id"), nullable=True)  # 变体派生
    variant_params = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Component(Base):
    __tablename__ = "components"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), default="standard")  # standard/custom
    comp_type = Column(String(100), nullable=True)  # 压铆BSO-M6/沉头M4/过孔φ4
    spec_json = Column(JSON, nullable=True)
    step_template = Column(String(512), nullable=True)
    visibility = Column(String(20), default="enterprise")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# === 智能记忆系统 ===

class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, nullable=True)
    mem_type = Column(String(30), nullable=False)  # dialog/decision/correction/preference/knowledge
    content = Column(JSON, nullable=False)
    context = Column(String(255), nullable=True)  # 触发场景
    confidence = Column(Float, default=0.7)
    use_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, nullable=True)


# === AI会话系统 (Phase 4) ===

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    task_id = Column(String(36), ForeignKey("conversion_tasks.id"), nullable=True)
    stage = Column(String(20), nullable=False, default="init")
    # init/classify/understand/plan/convert/audit/done/failed
    messages = Column(JSON, default=list)
    # [{role: "ai"/"user", content: "...", stage: "classify", timestamp: "..."}]
    context = Column(JSON, default=dict)
    # {process, description, plan, features_json, veritas_path, dxf_path}
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
