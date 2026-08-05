"""Beacon专家 - 项目管理路由.

按项目组织多零件: 每个项目指定一个 work_dir, 项目所有文件
(STP输入/DXF输出/技术要求)存到该用户指定目录。技术要求为项目级
(一套适用于全项目零件)。三级权限按 scope 过滤。
"""
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user, scope_filter
from src.config import MAX_UPLOAD_BYTES, ALLOWED_SUFFIX
from src.database import get_db, User, Project, Drawing, Conversation, ConversionTask

router = APIRouter(prefix="/api/projects", tags=["项目"])

_VALID_SCOPES = ("personal", "dept", "enterprise")
_VALID_STATUS = ("active", "archived")


# ---------- 工具函数 ----------

def _ensure_work_dirs(work_dir: str) -> None:
    """确保 work_dir 及 inputs/outputs 子目录存在 (makedirs 自带建父目录)."""
    os.makedirs(os.path.join(work_dir, "inputs"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "outputs"), exist_ok=True)


def _validate_work_dir(work_dir: str) -> str:
    """校验 work_dir: 必须绝对路径, 返回规范化后的路径."""
    if not work_dir or not work_dir.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="work_dir 不能为空")
    if not os.path.isabs(work_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"work_dir 必须为绝对路径 (收到相对路径: {work_dir})",
        )
    return os.path.abspath(work_dir)


def _serialize(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "owner_id": p.owner_id,
        "scope": p.scope,
        "dept_id": p.dept_id,
        "work_dir": p.work_dir,
        "tech_reqs": p.tech_reqs or {},
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _get_visible_project(pid: int, user: User, db: Session) -> Project:
    """取项目并校验行级权限, 不存在或不可见则抛 404/403."""
    q = db.query(Project).filter(Project.id == pid)
    visible = scope_filter(q, Project, user).first()
    if not visible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在或无权访问")
    return visible


# ---------- Pydantic 模型 ----------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scope: str = Field("enterprise", description="personal/dept/enterprise")
    work_dir: str = Field(..., description="项目工作目录绝对路径 (必填)")
    tech_reqs: Optional[dict] = Field(None, description="项目级技术要求 {material,tolerance,volume,priority,special}")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    work_dir: Optional[str] = None
    tech_reqs: Optional[dict] = None
    status: Optional[str] = None


# ---------- 端点 ----------

@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_project(
    req: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建项目: 校验 work_dir + 建目录 + 存 DB.

    work_dir 必填 (用户指定的工作目录), 若不存在则 mkdir -p, 已存在则直接用。
    """
    if req.scope not in _VALID_SCOPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope 必须为 { _VALID_SCOPES }")
    work_dir = _validate_work_dir(req.work_dir)
    _ensure_work_dirs(work_dir)

    tech_reqs = req.tech_reqs or {}
    # 合并默认键, 保证 tech_reqs 结构稳定
    default_keys = {"material": None, "tolerance": None, "volume": None, "priority": None, "special": None}
    default_keys.update({k: v for k, v in tech_reqs.items() if k in default_keys})

    proj = Project(
        name=req.name,
        owner_id=user.id,
        scope=req.scope,
        dept_id=user.dept_id if req.scope == "dept" else None,
        work_dir=work_dir,
        tech_reqs=default_keys,
        status="active",
    )
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return _serialize(proj)


@router.get("")
@router.get("/")
def list_projects(
    status_filter: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """项目列表 (scope_filter 行级过滤). 可选 ?status_filter=active/archived."""
    q = db.query(Project)
    q = scope_filter(q, Project, user)
    if status_filter:
        q = q.filter(Project.status == status_filter)
    items = q.order_by(Project.created_at.desc()).all()
    return {"total": len(items), "projects": [_serialize(p) for p in items]}


@router.get("/{pid}")
def get_project(
    pid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """项目详情 + 聚合文件列表 (Drawing/Conversation/ConversionTask)."""
    proj = _get_visible_project(pid, user, db)

    drawings = (
        db.query(Drawing).filter(Drawing.project_id == pid)
        .order_by(Drawing.created_at.desc()).all()
    )
    tasks = (
        db.query(ConversionTask).filter(ConversionTask.project_id == pid)
        .order_by(ConversionTask.created_at.desc()).all()
    )
    convos = (
        db.query(Conversation).filter(Conversation.project_id == pid)
        .order_by(Conversation.created_at.desc()).all()
    )

    return {
        **_serialize(proj),
        "files": {
            "drawings": [
                {
                    "id": d.id, "name": d.name, "step_path": d.step_path,
                    "dxf_path": d.dxf_path, "process": d.process,
                    "task_id": d.task_id,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in drawings
            ],
            "tasks": [
                {"id": t.id, "status": t.status, "stp_path": t.stp_path, "dxf_path": t.dxf_path}
                for t in tasks
            ],
            "conversations": [{"id": c.id, "stage": c.stage} for c in convos],
        },
    }


@router.patch("/{pid}")
def update_project(
    pid: int,
    req: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新项目 name/work_dir/tech_reqs/status.

    改 work_dir 时只记忆新路径 + 建目录, v1 不迁移文件。
    仅 owner 或 admin 可改。
    """
    proj = _get_visible_project(pid, user, db)
    if proj.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅作者或管理员可修改")

    data = req.dict(exclude_unset=True)

    if "status" in data and data["status"] is not None:
        if data["status"] not in _VALID_STATUS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"status 必须为 { _VALID_STATUS }")

    if "work_dir" in data and data["work_dir"] is not None:
        new_wd = _validate_work_dir(data["work_dir"])
        _ensure_work_dirs(new_wd)
        data["work_dir"] = new_wd  # 记忆新路径, 不迁移文件

    if "tech_reqs" in data and data["tech_reqs"] is not None:
        merged = dict(proj.tech_reqs or {})
        merged.update({k: v for k, v in data["tech_reqs"].items()})
        data["tech_reqs"] = merged

    for k, v in data.items():
        setattr(proj, k, v)

    db.commit()
    db.refresh(proj)
    return {"id": proj.id, "updated": list(data.keys()), "project": _serialize(proj)}


@router.post("/{pid}/files", status_code=status.HTTP_201_CREATED)
async def add_project_file(
    pid: int,
    file: UploadFile = File(..., description="STP/STEP 输入文件"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """加文件到项目: 上传 STP 到 {work_dir}/inputs/, 建 Drawing 关联.

    只登记输入文件, 实际转换由现有 chat/convert 流程触发。
    """
    proj = _get_visible_project(pid, user, db)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件超限({len(content) // 1024 // 1024}MB>{MAX_UPLOAD_BYTES // 1024 // 1024}MB)",
        )
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"仅支持 {ALLOWED_SUFFIX}")

    inputs_dir = os.path.join(proj.work_dir, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    # 防止碰撞: 同名文件追加短时间戳后缀
    base, ext = os.path.splitext(file.filename)
    dest = os.path.join(inputs_dir, file.filename)
    if os.path.exists(dest):
        dest = os.path.join(inputs_dir, f"{base}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{ext}")
    with open(dest, "wb") as f:
        f.write(content)

    drawing = Drawing(
        user_id=user.id,
        project_id=pid,
        name=base,
        step_path=dest,
        visibility="personal",
    )
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return {
        "drawing_id": drawing.id,
        "project_id": pid,
        "name": drawing.name,
        "step_path": dest,
        "note": "文件已登记, 实际转换请通过 chat/convert 流程触发",
    }


@router.get("/{pid}/files")
def list_project_files(
    pid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列项目文件: 输入 STP / 输出 DXF / 状态 / 技术要求."""
    proj = _get_visible_project(pid, user, db)
    drawings = (
        db.query(Drawing).filter(Drawing.project_id == pid)
        .order_by(Drawing.created_at.desc()).all()
    )
    return {
        "project_id": pid,
        "work_dir": proj.work_dir,
        "tech_reqs": proj.tech_reqs or {},
        "files": [
            {
                "drawing_id": d.id,
                "name": d.name,
                "input_stp": d.step_path,
                "output_dxf": d.dxf_path,
                "dxf_ready": bool(d.dxf_path and os.path.exists(d.dxf_path)),
                "process": d.process,
                "task_id": d.task_id,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in drawings
        ],
    }


@router.delete("/{pid}")
def archive_project(
    pid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """归档项目 (status=archived, 不删文件). 仅 owner 或 admin."""
    proj = _get_visible_project(pid, user, db)
    if proj.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅作者或管理员可归档")
    proj.status = "archived"
    db.commit()
    db.refresh(proj)
    return {"id": proj.id, "status": proj.status, "archived": True, "note": "文件未删除, 可恢复"}
