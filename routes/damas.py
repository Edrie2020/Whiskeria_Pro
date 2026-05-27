# routes/damas.py

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal
from services.auth_service import obtener_usuario_sesion
from services.time_service import obtener_ahora_local  
import models
from datetime import datetime
from database import get_db
import os
import shutil
import uuid


router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------
# 1. PANEL DE ADMINISTRACIÓN DE PERSONAL (VER TODOS LOS ROLES PERMITIDOS)
# ---------------------------------------------------------
@router.get("/admin_personal")
def admin_personal(request: Request, db: Session = Depends(get_db)):
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera", "jefe_guillermo", "encargado"]:
        return RedirectResponse(url="/", status_code=303)

    todas = db.query(models.Dama).all()
    ahora = obtener_ahora_local()
    
    activas = []
    inactivas = []
    
    # EVITAR CRASH POR VALORES NONE Y UNIFICAR HORA DE COMPARACIÓN
    for d in todas:
        if not d.ultima_asistencia:
            d.ultima_asistencia = ahora  # Asignación segura en memoria
            
        diff_days = (ahora - d.ultima_asistencia).days
        if diff_days <= 30:
            activas.append(d)
        else:
            inactivas.append(d)
            
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
# 2. AGREGAR NUEVA DAMA (SÓLO ADMIN1, ADMINISTRADOR, CAJERA)
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
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uploads_path = os.path.join(base_path, "static", "uploads")
    os.makedirs(uploads_path, exist_ok=True)
    
    ext = os.path.splitext(foto.filename)[1].lower()
    nombre_unico = f"{uuid.uuid4()}{ext}"
    
    file_location = os.path.join(uploads_path, nombre_unico)
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(foto.file, buffer)

    # USAR OBTENER_AHORA_LOCAL() EN LUGAR DE DATETIME.NOW()
    nueva = models.Dama(
        nombre_artistico=nombre_artistico.upper(),
        nombre_real=nombre_real,
        whatsapp=whatsapp,
        rut=rut,
        tipo_documento=tipo_documento,
        es_bailarina=(es_bailarina == "on"),
        foto_url=f"/static/uploads/{nombre_unico}",
        esta_activa=True,
        ultima_asistencia=obtener_ahora_local()
    )

    db.add(nueva)
    db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 3. EDITAR FICHA (SÓLO ADMIN1, ADMINISTRADOR, CAJERA)
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
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera"]:
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
# 4. REACTIVAR (SÓLO ADMIN1, ADMINISTRADOR, CAJERA)
# ---------------------------------------------------------
@router.post("/reactivar_dama/{dama_id}")
async def reactivar_dama(request: Request, dama_id: int, db: Session = Depends(get_db)):
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        dama.ultima_asistencia = obtener_ahora_local()
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
# ELIMINAR DAMA (SÓLO ADMIN1, ADMINISTRADOR, CAJERA)
# ---------------------------------------------------------
@router.post("/eliminar_dama/{dama_id}")
async def eliminar_dama(request: Request, dama_id: int, db: Session = Depends(get_db)):
    user_role = obtener_usuario_sesion(request)[1]
    username = request.cookies.get("session_user")
    
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        # 📝 AUDITORÍA
        log = models.LogAuditoria(
            usuario=username, 
            accion=f"ELIMINÓ FICHA DE DAMA: {dama.nombre_artistico}"
        )
        db.add(log)
        db.delete(dama)
        db.commit()

    return RedirectResponse(url="/admin_personal", status_code=303)