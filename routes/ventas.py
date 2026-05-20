from fastapi import APIRouter, Form, Depends, Request # <--- Añadimos Request
from typing import Optional, List
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from services.config_service import obtener_config
from services.ventas_service import calcular_venta_detallada
from datetime import datetime
from services.time_service import obtener_ahora_local
from database import get_db

router = APIRouter()

# ---------------------------------------------------------
# 1. REGISTRAR VENTA INDIVIDUAL
# ---------------------------------------------------------
@router.post("/registrar_venta")
async def registrar_venta(
    request: Request, # <--- Importante para la seguridad
    dama_id: Optional[int] = Form(None), 
    mesero: Optional[str] = Form(None),  
    metodo_pago: Optional[str] = Form(None), 
    tier_precio: int = Form(0),
    extra_tipo: Optional[str] = Form(None),
    producto_id: Optional[int] = Form(None),
    monto_casa_manual: int = Form(0),
    monto_chica_manual: int = Form(0),
    cliente_nombre: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # 🔒 VERIFICACIÓN DE SESIÓN
    if not request.cookies.get("session_user"):
        return RedirectResponse(url="/login", status_code=303)

    if not dama_id or not mesero or not metodo_pago:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    
    # Cálculos de dinero (Lógica de negocio)
    total, pago_chica, pago_casa = calcular_venta_detallada(
        tier_precio, extra_tipo, monto_casa_manual, monto_chica_manual
    )

    if tier_precio == 0:
        nombre_serv = "SALIDA MANUAL"
    else:
        nombre_serv = f"TRAGO {tier_precio // 1000}K"
    
    if extra_tipo:
        nombre_serv += f" + {extra_tipo}"

    nueva_venta = models.Venta(
        dama_id=dama_id,
        servicio=nombre_serv.upper(),
        monto=total,
        comision_chica=pago_chica,
        ganancia_casa=pago_casa,
        turno=conf.turno_activo,
        mesero=mesero,
        metodo_pago=metodo_pago,
        cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
        producto_id=producto_id,
        fecha=obtener_ahora_local()
    )
    db.add(nueva_venta)
    db.commit() # <--- ¡Perfecto, ya estaba aquí!
    return RedirectResponse(url="/", status_code=303)

# ---------------------------------------------------------
# 2. REGISTRAR RONDA DE MESA
# ---------------------------------------------------------
@router.post("/registrar_ronda_mesa")
async def registrar_ronda_mesa(
    request: Request, # <--- Seguridad
    ids_chicas: List[int] = Form(...), 
    mesero: str = Form(...),
    metodo_pago: str = Form(...),
    tier_precio: int = Form(...),           
    extra_tipo: Optional[str] = Form(None),
    producto_id: Optional[int] = Form(None),
    cliente_nombre: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # 🔒 VERIFICACIÓN DE SESIÓN
    if not request.cookies.get("session_user"):
        return RedirectResponse(url="/login", status_code=303)

    conf = obtener_config(db)
    ahora = obtener_ahora_local()

    for d_id in ids_chicas:
        total, chica, casa = calcular_venta_detallada(tier_precio, extra_tipo)
        
        nombre_serv = f"MESA: TRAGO {tier_precio//1000}K"
        if extra_tipo: nombre_serv += f" + {extra_tipo}"

        nueva_v = models.Venta(
            dama_id=d_id,
            servicio=nombre_serv.upper(),
            monto=total,
            comision_chica=chica,
            ganancia_casa=casa,
            turno=conf.turno_activo,
            mesero=mesero,
            metodo_pago=metodo_pago,
            cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
            producto_id=producto_id,
            fecha=ahora
        )
        db.add(nueva_v)

    db.commit()
    return RedirectResponse(url="/", status_code=303)

# ---------------------------------------------------------
# 3. VENTA CLIENTE SOLO
# ---------------------------------------------------------
# ---------------------------------------------------------
# 3. VENTA CLIENTE SOLO (CORREGIDA PARA DEBITAR STOCK)
# ---------------------------------------------------------
@router.post("/registrar_venta_cliente")
async def registrar_venta_cliente(
    request: Request,
    monto: Optional[str] = Form(None),
    mesero: Optional[str] = Form(None),
    trago: Optional[str] = Form(None),
    metodo_pago: str = Form(...),
    cliente_nombre: Optional[str] = Form(None),
    producto_id: Optional[int] = Form(None), # 📦 1. AÑADIMOS ESTO
    db: Session = Depends(get_db)
):
    # 🔒 VERIFICACIÓN DE SESIÓN
    if not request.cookies.get("session_user"):
        return RedirectResponse(url="/login", status_code=303)

    if not monto or not mesero:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    try:
        val_monto = float(monto)
    except:
        val_monto = 0.0
        
    nueva_venta = models.Venta(
        dama_id=None, 
        servicio=f"CLIENTE: {trago}".upper(), 
        monto=val_monto, 
        comision_chica=0, 
        ganancia_casa=val_monto, 
        turno=conf.turno_activo,
        mesero=mesero,
        metodo_pago=metodo_pago,
        cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
        producto_id=producto_id, # 📦 2. GUARDAMOS EL ID DEL PRODUCTO PARA EL STOCK
        fecha=obtener_ahora_local()
    )
    db.add(nueva_venta)
    db.commit()
    return RedirectResponse(url="/", status_code=303)