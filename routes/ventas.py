from fastapi import APIRouter, Form, Depends, Request
from typing import Optional, List
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import models
from services.config_service import obtener_config
from services.ventas_service import calcular_venta_detallada
from services.time_service import obtener_ahora_local
from database import get_db
from services.auth_service import obtener_usuario_sesion

router = APIRouter()

# ---------------------------------------------------------
# 1. REGISTRAR VENTA INDIVIDUAL
# ---------------------------------------------------------
@router.post("/registrar_venta")
async def registrar_venta(
    request: Request,
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
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    if not dama_id or not mesero or not metodo_pago:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    
    total, pago_chica, pago_casa = calcular_venta_detallada(
        tier_precio, extra_tipo, monto_casa_manual, monto_chica_manual
    )

    # CONSTRUCCIÓN DINÁMICA DE LA DESCRIPCIÓN DEL SERVICIO
    servicios_lista = []
    if tier_precio > 0:
        servicios_lista.append(f"TRAGO {tier_precio // 1000}K")
    if extra_tipo:
        servicios_lista.append(extra_tipo)
    if monto_casa_manual > 0 or monto_chica_manual > 0:
        servicios_lista.append("SALIDA MANUAL")
        
    nombre_serv = " + ".join(servicios_lista) if servicios_lista else "VENTA INDIVIDUAL"

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
    db.commit()
    return RedirectResponse(url="/", status_code=303)

# ---------------------------------------------------------
# 2. REGISTRAR RONDA DE MESA
# ---------------------------------------------------------
@router.post("/registrar_ronda_mesa")
async def registrar_ronda_mesa(
    request: Request,
    ids_chicas: List[int] = Form(...), 
    mesero: str = Form(...),
    metodo_pago: str = Form(...),
    tier_precio: int = Form(...),           
    extra_tipo: Optional[str] = Form(None),
    producto_id: Optional[int] = Form(None),
    cliente_nombre: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    ahora = obtener_ahora_local()

    for d_id in ids_chicas:
        total, chica, casa = calcular_venta_detallada(tier_precio, extra_tipo)
        
        nombre_serv = f"MESA: TRAGO {tier_precio//1000}K"
        if extra_tipo: 
            nombre_serv += f" + {extra_tipo}"

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
@router.post("/registrar_venta_cliente")
async def registrar_venta_cliente(
    request: Request,
    monto: Optional[str] = Form(None),
    mesero: Optional[str] = Form(None),
    trago: Optional[str] = Form(None),
    metodo_pago: str = Form(...),
    cliente_nombre: Optional[str] = Form(None),
    producto_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    if not monto or not mesero:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    try:
        val_monto = float(monto)
    except:
        val_monto = 0.0
        
    nueva_venta = models.Venta(
        dama_id=None, 
        servicio=f"CLIENTE: {trago}".upper() if trago else "CLIENTE SOLO", 
        monto=val_monto, 
        comision_chica=0, 
        ganancia_casa=val_monto, 
        turno=conf.turno_activo,
        mesero=mesero,
        metodo_pago=metodo_pago,
        cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
        producto_id=producto_id,
        fecha=obtener_ahora_local()
    )
    db.add(nueva_venta)
    db.commit()
    return RedirectResponse(url="/", status_code=303)
# routes/ventas.py
import json

@router.post("/registrar_venta_multi_cliente")
async def registrar_venta_multi_cliente(
    request: Request,
    mesero: str = Form(...),
    monto_total: float = Form(...),
    productos_json: str = Form(...),  # Recibe el carrito serializado en JSON desde el frontend
    metodo_pago: str = Form(...),
    cliente_nombre: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    conf = obtener_config(db)
    ahora = obtener_ahora_local()

    try:
        items = json.loads(productos_json)  # Lista de objetos [{"id": int, "nombre": str, "cantidad": int}]
    except Exception:
        return RedirectResponse(url="/?error=json_invalido", status_code=303)

    if not items:
        return RedirectResponse(url="/?error=boleta_vacia", status_code=303)

    # 1. Separamos el primer item para registrarlo como la transacción comercial principal (lleva el monto cobrado)
    primer_item = items[0]
    id_principal = primer_item["id"]
    cant_principal = primer_item["cantidad"]
    
    # Construimos la descripción resumida del servicio
    nombres_servicios = [f"{it['cantidad']}x {it['nombre']}" for it in items]
    descripcion_boleta = f"BOLETA: " + " + ".join(nombres_servicios)

    venta_principal = models.Venta(
        dama_id=None,
        servicio=descripcion_boleta.upper(),
        monto=monto_total,
        comision_chica=0.0,
        ganancia_casa=monto_total,
        turno=conf.turno_activo,
        mesero=mesero,
        metodo_pago=metodo_pago,
        cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
        producto_id=id_principal,
        fecha=ahora
    )
    db.add(venta_principal)

    # 2. Si el producto principal se consumió en cantidad superior a 1, registramos el resto con valor $0 para el stock
    for _ in range(cant_principal - 1):
        venta_extra = models.Venta(
            dama_id=None,
            servicio=f"CANT. EXTRA: {primer_item['nombre']}".upper(),
            monto=0.0,
            comision_chica=0.0,
            ganancia_casa=0.0,
            turno=conf.turno_activo,
            mesero=mesero,
            metodo_pago=metodo_pago,
            cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
            producto_id=id_principal,
            fecha=ahora
        )
        db.add(venta_extra)

    # 3. Registramos todos los productos secundarios del carrito con valor $0 para descontar stock
    for extra_item in items[1:]:
        id_extra = extra_item["id"]
        cant_extra = extra_item["cantidad"]
        for _ in range(cant_extra):
            venta_extra = models.Venta(
                dama_id=None,
                servicio=f"ACOMPAÑAMIENTO: {extra_item['nombre']}".upper(),
                monto=0.0,
                comision_chica=0.0,
                ganancia_casa=0.0,
                turno=conf.turno_activo,
                mesero=mesero,
                metodo_pago=metodo_pago,
                cliente_nombre=cliente_nombre.upper() if cliente_nombre else None,
                producto_id=id_extra,
                fecha=ahora
            )
            db.add(venta_extra)

    db.commit()
    return RedirectResponse(url="/", status_code=303)