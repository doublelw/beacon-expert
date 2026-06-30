"""Beacon专家 - JWT认证 + RBAC权限."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from src.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS
from src.database import get_db, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
security = HTTPBearer()

ROLE_HIERARCHY = {"admin": 4, "dept_manager": 3, "engineer": 2, "viewer": 1}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: int, email: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的token")


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(creds.credentials)
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


def require_role(min_role: str):
    """RBAC装饰器: require_role('admin') / require_role('engineer')."""
    min_level = ROLE_HIERARCHY.get(min_role, 0)

    def checker(user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(user.role, 0)
        if user_level < min_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"需要{min_role}权限")
        return user

    return checker


def scope_filter(query, model, user: User):
    """知识库/图纸三级权限行级过滤.

    admin: 全量
    其它: personal(自己) + dept(同部门) + enterprise(全员)
    """
    from sqlalchemy import or_, and_

    if user.role == "admin":
        return query

    conditions = [model.owner_id == user.id]  # personal
    if user.dept_id:
        conditions.append(and_(
            getattr(model, 'scope', None) == 'dept' if hasattr(model, 'scope') else False,
            getattr(model, 'dept_id', None) == user.dept_id if hasattr(model, 'dept_id') else False
        ))
    # enterprise
    if hasattr(model, 'scope'):
        conditions.append(model.scope == 'enterprise')
    elif hasattr(model, 'visibility'):
        conditions.append(model.visibility == 'enterprise')

    return query.filter(or_(*conditions))
