"""Beacon专家 - 认证/用户路由."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.auth import (
    get_current_user, require_role, hash_password, verify_password, create_token,
)
from src.database import get_db, User

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _valid_email(v: str) -> str:
    if "@" not in v or len(v) < 3:
        raise ValueError("无效的邮箱格式")
    return v.lower().strip()


class RegisterReq(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    username: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _valid_email(v)


class LoginReq(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _valid_email(v)


@router.post("/register")
def register(req: RegisterReq, db: Session = Depends(get_db)):
    """注册: bcrypt哈希 → 存User → 返回user_id + JWT."""
    exists = db.query(User).filter(User.email == req.email).first()
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已注册")
    user = User(
        email=req.email,
        username=req.username,
        password_hash=hash_password(req.password),
        role="engineer",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(user.id, user.email, user.role)
    return {"user_id": user.id, "token": token}


@router.post("/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    """登录: verify → 返回JWT."""
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    token = create_token(user.id, user.email, user.role)
    return {"token": token, "user_id": user.id, "role": user.role}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """当前用户信息."""
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "dept_id": user.dept_id,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


@router.get("/list")
def list_users(admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    """用户列表 (仅admin)."""
    users = db.query(User).order_by(User.id).all()
    return {
        "total": len(users),
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "role": u.role,
                "dept_id": u.dept_id,
                "is_active": u.is_active,
            }
            for u in users
        ],
    }
