# START OF FILE routes/damas.py
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
from typing import Optional

# Importar PIL para compresión ligera defensiva (si Pillow está instalado)
try:
    from PIL import Image
    pillow_disponible = True
except ImportError:
    pillow_disponible = False

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def optimizar_y_guardar_imagen(foto: UploadFile, uploads_path: str) -> str:
    """Optimiza, comprime y guarda la imagen subida para ahorrar espacio y mejorar carga."""
    ext = os.path.splitext(foto.filename)[1].lower()
    if not ext:
        ext = ".jpg"
    nombre_unico = f"{uuid.uuid4()}{ext}"
    file_location = os.path.join(uploads_path, nombre_unico)

    if pillow_disponible and ext in [".jpg", ".jpeg", ".png"]:
        try:
            # Compresión y redimensionamiento dinámico defensivo con Pillow
            img = Image.open(foto.file)
            # Convertir a RGB si es necesario (evita error en formato PNG transparente)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            # Redimensionar si la imagen es excesivamente grande
            max_size = 900
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size))
            
            # Guardar con compresión de calidad 75
            img.save(file_location, "JPEG", quality=75, optimize=True)
            return f"/static/uploads/{nombre_unico}"
        except Exception as err:
            print(f"⚠️ Error al optimizar con Pillow: {str(err)}")
            # Fallback si falla la compresión

    # Guardar copia física directa (si Pillow no está o falla)
    foto.file.seek(0)
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(foto.file, buffer)
    return f"/static/uploads/{nombre_unico}"


# ---------------------------------------------------------
# 1. PANEL DE ADMINISTRACIÓN DE PERSONAL
# ---------------------------------------------------------
@router.get("/admin_personal")
def admin_personal(request: Request, db: Session = Depends(get_db)):
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera", "jefe_guillermo", "encargado"]:
        return RedirectResponse(url="/", status_code=303)

    todas = db.query(models.Dama).filter(models.Dama.borrada == False).all()
    ahora = obtener_ahora_local()
    
    activas = []
    inactivas = []
    
    for d in todas:
        if not d.ultima_asistencia:
            d.ultima_asistencia = ahora
            
        diff_days = (ahora - d.ultima_asistencia).days
        if diff_days <= 30:
            activas.append(d)
        else:
            inactivas.append(d)
            
    garzones = db.query(models.Mesero).all()
    error_msg = request.query_params.get("error")

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "activas": activas,
            "inactivas": inactivas,
            "garzones": garzones,
            "role": user_role,
            "error_msg": error_msg,
            "username": request.cookies.get("session_user")
        }
    )

# ---------------------------------------------------------
# 2. AGREGAR NUEVA DAMA (CON COMPRESIÓN)
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
    
    # Guarda de forma comprimida si es posible
    ruta_foto_url = optimizar_y_guardar_imagen(foto, uploads_path)

    nueva = models.Dama(
        nombre_artistico=nombre_artistico.upper().strip(),
        nombre_real=nombre_real.strip(),
        whatsapp=whatsapp.strip(),
        rut=rut.strip(),
        tipo_documento=tipo_documento,
        es_bailarina=(es_bailarina == "on"),
        foto_url=ruta_foto_url,
        esta_activa=True,
        ultima_asistencia=obtener_ahora_local()
    )

    db.add(nueva)
    db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 3. EDITAR FICHA (CON ACTUALIZACIÓN OPCIONAL DE FOTO DE PERFIL)
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
    foto: Optional[UploadFile] = File(None),  # Carga opcional de nueva foto
    db: Session = Depends(get_db)
):
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        dama.nombre_artistico = nombre_artistico.upper().strip()
        dama.nombre_real = nombre_real.strip()
        dama.rut = rut.strip()
        dama.whatsapp = whatsapp.strip()
        dama.es_bailarina = (es_bailarina == "on")
        
        # Si se subió un nuevo archivo de foto, se optimiza y se actualiza
        if foto and foto.filename:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            uploads_path = os.path.join(base_path, "static", "uploads")
            os.makedirs(uploads_path, exist_ok=True)
            
            nueva_foto_url = optimizar_y_guardar_imagen(foto, uploads_path)
            dama.foto_url = nueva_foto_url
            
        db.commit()
    return RedirectResponse(url="/admin_personal", status_code=303)

# ---------------------------------------------------------
# 4. REACTIVAR DAMA
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
# 6. ELIMINAR DAMA (SOFT DELETE)
# ---------------------------------------------------------
@router.post("/eliminar_dama/{dama_id}")
async def eliminar_dama(request: Request, dama_id: int, db: Session = Depends(get_db)):
    user_role = obtener_usuario_sesion(request)[1]
    username = request.cookies.get("session_user")
    
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    if dama:
        dama.borrada = True
        log = models.LogAuditoria(
            usuario=username, 
            accion=f"ELIMINÓ FICHA DE DAMA: {dama.nombre_artistico} (ELIMINACIÓN SUAVE)"
        )
        db.add(log)
        db.commit()

    return RedirectResponse(url="/admin_personal", status_code=303)
# END OF FILE routes/damas.py