from fastapi import APIRouter, Form, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import models
from datetime import time, timedelta
from services.asistencia_service import registrar_asistencia
from services.config_service import obtener_config
from services.time_service import obtener_ahora_local
from database import get_db
from services.auth_service import obtener_usuario_sesion

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------
# 1. VER PANEL DE ASISTENCIA (MIGRADO DESDE MAIN.PY)
# ---------------------------------------------------------
@router.get("/asistencia", response_class=HTMLResponse)
async def asistencia_page(request: Request, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    
    # Seguridad: Solo admin1, jefe_guillermo, admin2 y cajera pueden ver esta página
    if not username or user_role not in ["admin1", "jefe_guillermo", "admin2", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    
    # Calculamos la fecha operativa de Chile de manera segura
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        hoy = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        hoy = ahora.strftime("%Y-%m-%d")

    asistencias_hoy = db.query(models.Asistencia).filter(
        models.Asistencia.fecha == hoy,
        models.Asistencia.turno == conf.turno_activo
    ).all()

    ids_presentes = [a.dama_id for a in asistencias_hoy]
    ausentes = db.query(models.Dama).filter(models.Dama.esta_activa == True, ~models.Dama.id.in_(ids_presentes)).all()
    presentes = db.query(models.Dama).filter(models.Dama.id.in_(ids_presentes)).all()

    return templates.TemplateResponse(
        request=request,
        name="asistencia.html",
        context={
            "ausentes": ausentes, 
            "presentes": presentes,
            "turno": conf.turno_activo, 
            "estado_club": conf.estado_club,
            "role": user_role  # Enviamos el rol para bloquear clics si es de lectura
        }
    )

# ---------------------------------------------------------
# 2. MARCAR ASISTENCIA (DE AUSENTE A PRESENTE)
# ---------------------------------------------------------
@router.post("/marcar_asistencia")
async def marcar_asistencia(
    request: Request,
    dama_id: int = Form(...),
    tipo_llegada: str = Form(None),
    hora_libro: str = Form(None),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    
    # Seguridad: Solo admin1 y cajera pueden registrar asistencia
    if not username or user_role not in ["admin1", "cajera"]:
        return RedirectResponse(url="/asistencia", status_code=303)

    conf = obtener_config(db)
    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()

    nueva = registrar_asistencia(
        dama,
        conf.turno_activo,
        tipo_llegada,
        hora_libro
    )

    db.add(nueva)
    db.commit()
    return RedirectResponse(url="/asistencia", status_code=303)

# ---------------------------------------------------------
# 3. DAR SALIDA (BORRAR ASISTENCIA DE HOY)
# ---------------------------------------------------------
@router.post("/dar_salida/{dama_id}")
async def dar_salida(request: Request, dama_id: int, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    
    # Seguridad: Solo admin1 y cajera pueden quitar asistencias de salón
    if not username or user_role not in ["admin1", "cajera"]:
        return RedirectResponse(url="/asistencia", status_code=303)

    conf = obtener_config(db)
    
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        hoy_str = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        hoy_str = ahora.strftime("%Y-%m-%d")

    asistencia = db.query(models.Asistencia).filter(
        models.Asistencia.dama_id == dama_id,
        models.Asistencia.fecha == hoy_str,
        models.Asistencia.turno == conf.turno_activo
    ).first()

    if asistencia:
        dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
        if dama:
            if conf.turno_activo == "Turno 1":
                dama.dias_t1 = max(0, dama.dias_t1 - 1)
            else:
                dama.dias_t2 = max(0, dama.dias_t2 - 1)

        db.delete(asistencia)
        db.commit()

    return RedirectResponse(url="/asistencia", status_code=303)