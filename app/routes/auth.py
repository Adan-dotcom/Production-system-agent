from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import authenticate_user, create_access_token, get_current_user

router = APIRouter()


@router.post("/login")
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    access_token = create_access_token(
        data={"user_id": user.user_id, "username": user.username, "role": user.role}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
        },
    }


@router.get("/me")
def me(current_user=Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "is_active": current_user.is_active,
    }


@router.get("/operators")
def list_operators(db: Session = Depends(get_db)):
    """Returns active operator users so the UI can show a name-selection dropdown."""
    users = (
        db.query(models.User)
        .filter(models.User.role == "operator", models.User.is_active == True)
        .order_by(models.User.full_name.asc())
        .all()
    )
    return [
        {"user_id": u.user_id, "full_name": u.full_name, "username": u.username}
        for u in users
    ]


@router.post("/select-operator")
def select_operator(payload: schemas.OperatorSelectRequest, db: Session = Depends(get_db)):
    """Issues a JWT for an operator selected by name (no password required).
    Only works for users with role='operator'. Admins must use /auth/login.
    """
    user = db.query(models.User).filter(models.User.user_id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Operador no encontrado")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Operador inactivo")
    if user.role != "operator":
        raise HTTPException(status_code=403, detail="Solo operadores pueden usar este endpoint")

    access_token = create_access_token(
        data={"user_id": user.user_id, "username": user.username, "role": user.role}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
        },
    }