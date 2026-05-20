from fastapi import APIRouter, Form, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from datetime import date  # <--- Esto es vital para la fecha
from services.asistencia_service import registrar_asistencia
from services.config_service import obtener_config
from database import get_db
from services.auth_service import obtener_usuario_sesion

router = APIRouter()

# ---------------------------------------------------------
# MARCAR ASISTENCIA (DE AUSENTE A PRESENTE)
# ---------------------------------------------------------
@router.post("/marcar_asistencia")
async def marcar_asistencia(
    request: Request, # <--- Seguridad añadida
    dama_id: int = Form(...),
    tipo_llegada: str = Form(None),
    hora_libro: str = Form(None),
    db: Session = Depends(get_db)
):
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
# DAR SALIDA (BORRAR ASISTENCIA DE HOY)
# ---------------------------------------------------------
@router.post("/dar_salida/{dama_id}")
async def dar_salida(request: Request, dama_id: int, db: Session = Depends(get_db)):
    # 🔒 SEGURIDAD: Solo Jefes, Admin o Cajeras
    user_role = obtener_usuario_sesion(request)[1]
    if user_role not in ["jefe", "admin", "cajera"]:
        return RedirectResponse(url="/asistencia", status_code=303)

    conf = obtener_config(db)
    hoy_str = date.today().strftime("%Y-%m-%d")

    # 1. Buscamos la asistencia de hoy
    asistencia = db.query(models.Asistencia).filter(
        models.Asistencia.dama_id == dama_id,
        models.Asistencia.fecha == hoy_str,
        models.Asistencia.turno == conf.turno_activo
    ).first()

    if asistencia:
        # 2. Restar el día trabajado en la ficha
        dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
        if dama:
            if conf.turno_activo == "Turno 1":
                dama.dias_t1 = max(0, dama.dias_t1 - 1)
            else:
                dama.dias_t2 = max(0, dama.dias_t2 - 1)

        # 3. Borrar registro
        db.delete(asistencia)
        db.commit()

    return RedirectResponse(url="/asistencia", status_code=303)