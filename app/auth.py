from __future__ import annotations
import bcrypt
from fastapi import Request
from app.db import db_session
from app.models import User

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def get_user_by_username(username: str) -> User | None:
    with db_session() as s:
        return s.query(User).filter(User.username == username).first()

def get_user_by_id(user_id: int) -> User | None:
    with db_session() as s:
        return s.get(User, user_id)

def require_user(request: Request) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise PermissionError("not_authenticated")
    u = get_user_by_id(int(user_id))
    if not u:
        raise PermissionError("not_authenticated")
    return u

def login_session(request: Request, user: User) -> None:
    request.session["user_id"] = user.id

def logout_session(request: Request) -> None:
    request.session.pop("user_id", None)
