# routes/usuarios.py

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from services.auth_service import obtener_usuario_sesion
import models
from services.auth_service import obtener_hash
from database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 1. VER LA PÁGINA DE USUARIOS
@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_page(request: Request, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    
    # Permitir únicamente a admin1, administrador (poder total) y jefe_guillermo (gestor de accesos)
    if not username or user_role not in ["admin1", "administrador", "jefe_guillermo"]:
        return RedirectResponse(url="/", status_code=303)
    
    lista = db.query(models.Usuario).all()
    return templates.TemplateResponse(
        request=request, 
        name="usuarios.html", 
        context={"usuarios": lista, "role": user_role}
    )

# 2. CREAR USUARIO
@router.post("/usuarios/crear")
async def crear_usuario(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...), 
    rol: str = Form(...), 
    db: Session = Depends(get_db)
):
    username_sesion, user_role = obtener_usuario_sesion(request)
    if not username_sesion or user_role not in ["admin1", "administrador", "jefe_guillermo"]:
        return RedirectResponse(url="/", status_code=303)

    nuevo = models.Usuario(
        username=username.lower(),
        password_hash=obtener_hash(password),
        rol=rol
    )
    db.add(nuevo)
    db.commit()
    return RedirectResponse(url="/usuarios", status_code=303)

# 3. ELIMINAR USUARIO
@router.post("/usuarios/eliminar/{user_id}")
async def eliminar_usuario(request: Request, user_id: int, db: Session = Depends(get_db)):
    username_sesion, user_role = obtener_usuario_sesion(request)
    if not username_sesion or user_role not in ["admin1", "administrador", "jefe_guillermo"]:
        return RedirectResponse(url="/", status_code=303)
    
    user = db.query(models.Usuario).filter(models.Usuario.id == user_id).first()
    
    # Evitar que se eliminen cuentas raíces del sistema
    if user and user.username not in ["admin", "admin1"]:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/usuarios", status_code=303)

# 4. CAMBIAR CONTRASEÑA
@router.post("/usuarios/cambiar_password/{user_id}")
async def cambiar_password(request: Request, user_id: int, nueva_clave: str = Form(...), db: Session = Depends(get_db)):
    username_sesion, user_role = obtener_usuario_sesion(request)
    if not username_sesion or user_role not in ["admin1", "administrador", "jefe_guillermo"]:
        return RedirectResponse(url="/", status_code=303)

    user = db.query(models.Usuario).filter(models.Usuario.id == user_id).first()
    if user:
        user.password_hash = obtener_hash(nueva_clave)
        db.commit()
    return RedirectResponse(url="/usuarios", status_code=303)