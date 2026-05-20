# routes/auth.py
from fastapi import APIRouter, Depends, Form, Response, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

# Importaciones locales
import models
from database import get_db  # <-- Usamos la dependencia centralizada que creamos
from services.auth_service import verificar_password, firmar_valor  # <-- Traemos las funciones seguras

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 1. RUTA PARA MOSTRAR LA PÁGINA DE LOGIN
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request, 
        name="login.html", 
        context={"error": error}
    )

# 2. RUTA PARA PROCESAR EL FORMULARIO (LOGIN)
@router.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Buscamos al usuario por su nombre
    user = db.query(models.Usuario).filter(models.Usuario.username == username).first()
    
    # Si el usuario existe y la contraseña es correcta...
    if user and verificar_password(password, user.password_hash):
        redireccion = RedirectResponse(url="/", status_code=303)
        
        # 🔒 GUARDAMOS LAS COOKIES FIRMADAS CRIPTOGRÁFICAMENTE
        # Esto evita que un usuario cambie su rol a "jefe" usando la consola F12
        redireccion.set_cookie(
            key="session_user", 
            value=firmar_valor(user.username),  # <-- Guardamos ej: "admin.firma1234..."
            httponly=True, 
            max_age=259200
        )
        redireccion.set_cookie(
            key="session_role", 
            value=firmar_valor(user.rol),       # <-- Guardamos ej: "jefe.firma5678..."
            httponly=True, 
            max_age=259200
        )
        
        return redireccion
    
    # Si los datos están mal, lo devolvemos al login con mensaje de error
    return RedirectResponse(url="/login?error=Usuario o clave incorrectos", status_code=303)

# 3. RUTA PARA CERRAR SESIÓN
@router.get("/logout")
async def logout():
    redireccion = RedirectResponse(url="/login", status_code=303)
    # Borramos las cookies del navegador
    redireccion.delete_cookie("session_user")
    redireccion.delete_cookie("session_role")
    return redireccion