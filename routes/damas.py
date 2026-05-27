from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal
from services.auth_service import obtener_usuario_sesion
import models
from datetime import datetime
from database import get_db
import os
import shutil
import uuid


router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------
# 1. PANEL DE ADMINISTRACIÓN DE PERSONAL (SOLO JEFE)
# ---------------------------------------------------------
@router.get("/admin_personal")
def admin_personal(request: Request, db: Session = Depends(get_db)):
    # 🔒 SEGURIDAD: Solo el rol 'jefe' puede ver esta página
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "jefe_guillermo", "admin2", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    todas = db.query(models.Dama).all()
    # Lógica de inactivas por más de 30 días
    activas = [d for d in todas if (datetime.now() - d.ultima_asistencia).days <= 30]
    inactivas = [d for d in todas if (datetime.now() - d.ultima_asistencia).days > 30]
    garzones = db.query(models.Mesero).all()

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "activas": activas,
            "inactivas": inactivas,
            "garzones": garzones,
            "role": user_role,
            "username": request.cookies.get("session_user")
        }
    )

# ---------------------------------------------------------
# 2. AGREGAR NUEVA DAMA (SOLO JEFE)
# ---------------------------------------------------------
@router.post("/agregar_dama")
async def agregar_dama(
    request: Request,
    nombre_artistico: str = Form(...),
    nombre_real: str = Form(...),
    whatsapp: str = Form(...),
    rut: str = Form(...),
    tipo_documento: str = Form(...),
    es_bailarina: str = Form("off"),
    foto: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 🔒 SEGURIDAD
    if obtener_usuario_sesion(request)[1] != "admin1":
        return RedirectResponse(url="/", status_code=303)

    # Lógica de guardado de imagen con nombre de archivo único
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uploads_path = os.path.join(base_path, "static", "uploads")
    os.makedirs(uploads_path, exist_ok=True)
    
    # 📝 Separamos el nombre original de la extensión (ej: ".jpg" o ".png")
    ext = os.path.splitext(foto.filename)[1].lower()
    
    # 🔒 Generamos un nombre único usando UUIDv4 para evitar colisiones
    nombre_unico = f"{uuid.uuid4()}{ext}"
    
    # Guardamos el archivo con el nuevo nombre único
    file_location = os.path.join(uploads_path, nombre_unico)
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(foto.file, buffer)

    nueva = models.Dama(
        nombre_artistico=nombre_artistico.upper(),
        nombre_real=nombre_real,
        whatsapp=whatsapp,
        rut=rut,
        tipo_documento=tipo_documento,
        es_bailarina=(es_bailarina == "on"),
        foto_url=f"/static/uploads/{nombre_unico}", # <--- Guardamos la ruta única en la base de datos
        esta_activa=True,
        ultima_asistencia=datetime.now()
    )

    db.add(nueva)
    db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 3. EDITAR FICHA (SOLO JEFE)
# ---------------------------------------------------------
@router.post("/editar_dama/{dama_id}")
async def editar_dama(
    request: Request,
    dama_id: int,
    nombre_artistico: str = Form(...),
    nombre_real: str = Form(...),
    rut: str = Form(...),
    whatsapp: str = Form(...),
    es_bailarina: str = Form("off"),
    db: Session = Depends(get_db)
):
    # 🔒 SEGURIDAD
    if obtener_usuario_sesion(request)[1] != "admin1":
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        dama.nombre_artistico = nombre_artistico.upper()
        dama.nombre_real = nombre_real.upper()
        dama.rut = rut
        dama.whatsapp = whatsapp
        dama.es_bailarina = (es_bailarina == "on")
        db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 4. REACTIVAR (SOLO JEFE)
# ---------------------------------------------------------
@router.post("/reactivar_dama/{dama_id}")
async def reactivar_dama(request: Request, dama_id: int, db: Session = Depends(get_db)):
    if obtener_usuario_sesion(request)[1] != "admin1":
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        dama.ultima_asistencia = datetime.now()
        db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 5. CONTROL DE SHOWS/PISTA
# ---------------------------------------------------------
@router.post("/activar_baile")
async def activar_baile(
    request: Request,
    asistencia_id: int = Form(...), 
    cantidad: int = Form(...), 
    baila: str = Form("off"), 
    db: Session = Depends(get_db)
):
    # 🔒 SEGURIDAD (Incluso garzones podrían usar esto si les das permiso, 
    # pero por ahora lo dejamos bajo login general)
    if not request.cookies.get("session_user"):
        return RedirectResponse(url="/login", status_code=303)

    asis = db.query(models.Asistencia).filter(models.Asistencia.id == asistencia_id).first()
    if asis:
        asis.bailando_hoy = (baila == "on")
        asis.cantidad_shows = cantidad
        asis.bono_show = cantidad * 10000 
        db.commit()
    return RedirectResponse(url="/", status_code=303)

# ---------------------------------------------------------
# ELIMINAR DAMA (SÓLO NIVELES DE MANDO)
# ---------------------------------------------------------
@router.post("/eliminar_dama/{dama_id}")
async def eliminar_dama(request: Request, dama_id: int, db: Session = Depends(get_db)):
    # 🔒 SEGURIDAD: Solo Jefe, Admin o Cajera
    user_role = obtener_usuario_sesion(request)[1]
    username = request.cookies.get("session_user")
    
    if obtener_usuario_sesion(request)[1] != "admin1":
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        # 📝 AUDITORÍA: Guardamos quién borró a quién
        log = models.LogAuditoria(
            usuario=username, 
            accion=f"ELIMINÓ FICHA DE DAMA: {dama.nombre_artistico}"
        )
        db.add(log)
        
        # Borramos la dama
        db.delete(dama)
        db.commit()

    return RedirectResponse(url="/admin_personal", status_code=303)