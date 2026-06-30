"""Beacon专家 - 知识库路由."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from sqlalchemy.orm import Session

from src.auth import get_current_user, scope_filter
from src.database import get_db, User, Knowledge

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = ""
    category: str = "general"
    scope: str = "personal"  # personal/dept/enterprise
    tags: List[Any] = []


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None
    tags: Optional[List[Any]] = None


@router.post("/")
def create_knowledge(
    req: KnowledgeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建知识条目 (owner_id=当前用户, scope默认personal)."""
    if req.scope not in ("personal", "dept", "enterprise"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope必须为 personal/dept/enterprise")
    item = Knowledge(
        owner_id=user.id,
        title=req.title,
        content=req.content,
        category=req.category,
        scope=req.scope,
        tags=req.tags,
        dept_id=user.dept_id if req.scope == "dept" else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "owner_id": item.owner_id, "scope": item.scope}


@router.get("/")
def list_knowledge(
    category: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """知识列表 (scope_filter行级过滤)."""
    q = db.query(Knowledge)
    q = scope_filter(q, Knowledge, user)
    if category:
        q = q.filter(Knowledge.category == category)
    items = q.order_by(Knowledge.id.desc()).all()
    return {
        "total": len(items),
        "items": [
            {
                "id": k.id,
                "title": k.title,
                "category": k.category,
                "scope": k.scope,
                "owner_id": k.owner_id,
                "tags": k.tags,
                "updated_at": k.updated_at,
            }
            for k in items
        ],
    }


@router.get("/{kid}")
def get_knowledge(
    kid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """知识详情."""
    item = db.get(Knowledge, kid)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条目不存在")
    # 行级权限校验
    visible = scope_filter(db.query(Knowledge).filter(Knowledge.id == kid), Knowledge, user).first()
    if not visible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "category": item.category,
        "scope": item.scope,
        "owner_id": item.owner_id,
        "dept_id": item.dept_id,
        "tags": item.tags,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.put("/{kid}")
def update_knowledge(
    kid: int,
    req: KnowledgeUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新知识 (仅owner或admin)."""
    item = db.get(Knowledge, kid)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条目不存在")
    if item.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅作者或管理员可修改")
    data = req.dict(exclude_unset=True)
    if "scope" in data and data["scope"] not in ("personal", "dept", "enterprise"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope必须为 personal/dept/enterprise")
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "updated": list(data.keys())}
