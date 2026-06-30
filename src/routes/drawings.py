"""Beacon专家 - 图纸效率路由 (Phase 5).

图纸列表/搜索/变体派生。三级权限按 visibility 过滤。
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db, Drawing, User
from src.engine.derive import decide_method, derive_dxf_scale

router = APIRouter(prefix="/api/drawings", tags=["图纸效率"])


def _drawing_scope_filter(query, user: User):
    """Drawing 行级权限: admin全量; 其它 = personal(自己) + dept(同部门) + enterprise(全员)."""
    if user.role == "admin":
        return query
    conditions = [Drawing.user_id == user.id]  # personal
    if user.dept_id:
        # dept 级: 同部门 engineer 以上创建的图纸 (Drawing 无 dept_id, 通过创建者推断)
        conditions.append(Drawing.visibility == "dept")
    conditions.append(Drawing.visibility == "enterprise")
    return query.filter(or_(*conditions))


def _serialize(d: Drawing) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "user_id": d.user_id,
        "task_id": d.task_id,
        "step_path": d.step_path,
        "dxf_path": d.dxf_path,
        "process": d.process,
        "bbox": d.bbox,
        "features_summary": d.features_summary,
        "tags": d.tags or [],
        "category": d.category,
        "visibility": d.visibility,
        "parent_id": d.parent_id,
        "variant_params": d.variant_params,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("")
@router.get("/")
def list_drawings(
    category: Optional[str] = Query(None, description="按分类过滤"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图纸列表 (按 visibility 三级权限过滤)."""
    q = db.query(Drawing)
    q = _drawing_scope_filter(q, user)
    if category:
        q = q.filter(Drawing.category == category)
    drawings = q.order_by(Drawing.created_at.desc()).all()
    return {"total": len(drawings), "drawings": [_serialize(d) for d in drawings]}


@router.get("/search")
def search_drawings(
    q: str = Query(..., min_length=1, description="搜索关键词 (name/process/tags)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """搜索图纸: name / process / tags (LIKE 模糊匹配)."""
    query = db.query(Drawing)
    query = _drawing_scope_filter(query, user)
    kw = f"%{q}%"
    # tags 是 JSON 数组, SQLite 用 LIKE 兜底匹配字符串化内容
    query = query.filter(
        or_(
            Drawing.name.ilike(kw),
            Drawing.process.ilike(kw),
            Drawing.tags.cast(__import__('sqlalchemy').Text).ilike(kw),
        )
    )
    drawings = query.order_by(Drawing.created_at.desc()).all()
    return {"total": len(drawings), "query": q, "drawings": [_serialize(d) for d in drawings]}


class VariantReq(BaseModel):
    scale_factor: Optional[float] = Field(None, gt=0, description="缩放因子 (1.0=不变)")
    add_holes: Optional[List[dict]] = Field(None, description="新增孔位列表")
    material: Optional[str] = Field(None, description="材料变更")


@router.post("/{drawing_id}/variant")
def create_variant(
    drawing_id: int,
    req: VariantReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """从已有图纸派生变体 (缩放/加孔/换料).

    决策逻辑: scale_factor → 直接坐标缩放 (秒级); add_holes/material → rerun (重跑投影).
    """
    parent = db.query(Drawing).filter(Drawing.id == drawing_id).first()
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源图纸不存在")
    # 权限: 必须可见
    visible_ids = [
        d.id for d in _drawing_scope_filter(db.query(Drawing.id), user).all()
    ]
    if parent.id not in visible_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该图纸")

    params = req.dict(exclude_none=True)
    if not params:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供至少一个变体参数")

    method = decide_method(params)
    variant_params = {**params, "_method": method}

    # 缩放派生: 立即生成新 DXF
    new_dxf_path = None
    if method == "scale" and parent.dxf_path:
        try:
            new_dxf_path = derive_dxf_scale(parent.dxf_path, req.scale_factor)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"源 DXF 文件不存在: {parent.dxf_path}",
            )

    child = Drawing(
        user_id=user.id,
        name=f"{parent.name} (变体)",
        task_id=parent.task_id,
        step_path=parent.step_path,
        dxf_path=new_dxf_path,
        process=parent.process,
        bbox=parent.bbox,
        features_summary=parent.features_summary,
        tags=(parent.tags or []) + ["variant"],
        category=parent.category,
        visibility="personal",
        parent_id=parent.id,
        variant_params=variant_params,
    )
    db.add(child)
    db.commit()
    db.refresh(child)

    return {
        "id": child.id,
        "parent_id": parent.id,
        "method": method,
        "dxf_path": new_dxf_path,
        "variant_params": variant_params,
        "note": (
            "DXF已通过坐标缩放生成" if method == "scale"
            else "需重跑投影流程生成新DXF (rerun)"
        ),
    }
