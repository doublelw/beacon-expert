"""Beacon专家 - Phase 1 骨架测试."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.database import init_db, SessionLocal, User, Department
from src.auth import hash_password, verify_password, create_token, decode_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield
    # 清理测试数据
    db = SessionLocal()
    db.query(User).delete()
    db.query(Department).delete()
    db.commit()
    db.close()


class TestHealth:
    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "freecadcmd" in data
        assert "disk_free_mb" in data

    def test_root(self):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["name"] == "Beacon专家"


class TestPassword:
    def test_hash_and_verify(self):
        h = hash_password("test123")
        assert h != "test123"
        assert verify_password("test123", h)
        assert not verify_password("wrong", h)


class TestJWT:
    def test_create_and_decode(self):
        token = create_token(1, "test@beacon.com", "engineer")
        payload = decode_token(token)
        assert payload["email"] == "test@beacon.com"
        assert payload["role"] == "engineer"

    def test_invalid_token(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            decode_token("invalid.token.here")
        assert exc.value.status_code == 401


class TestDatabase:
    def test_create_user(self):
        db = SessionLocal()
        dept = Department(name="工程部")
        db.add(dept)
        db.flush()
        user = User(
            email="test@beacon.com",
            username="测试",
            password_hash=hash_password("pass"),
            role="engineer",
            dept_id=dept.id,
        )
        db.add(user)
        db.commit()
        assert user.id is not None
        assert user.role == "engineer"
        db.close()


class TestScopeFilter:
    def test_admin_sees_all(self):
        from src.auth import scope_filter
        from src.database import Knowledge
        db = SessionLocal()
        admin = User(email="admin@beacon.com", username="admin",
                     password_hash="x", role="admin")
        db.add(admin)
        db.flush()
        q = db.query(Knowledge)
        filtered = scope_filter(q, Knowledge, admin)
        # admin不过滤 = 原query
        assert filtered is q
        db.close()
