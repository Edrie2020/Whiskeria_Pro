from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
import models
from datetime import date, datetime, time, timedelta
from database import get_db

# Importamos las herramientas de seguridad y tiempo local
from services.time_service import obtener_ahora_local
from services.auth_service import obtener_usuario_sesion
from services.config_service import obtener_config

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------
# 1. VER EL PANEL DE STOCK
# ---------------------------------------------------------
@router.get("/stock", response_class=HTMLResponse)
async def stock_page(request: Request, fecha: str = None, turno: str = None, db: Session = Depends(get_db)):
    # 🔒 SEGURIDAD SEGURA: Solo ingresan usuarios verificados
    username, user_role = obtener_usuario_sesion(request)
    if not username:
        return RedirectResponse(url="/login", status_code=303)

    # Calculamos la fecha operativa real de Chile para el control antifraude
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_actual = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_actual = ahora.strftime("%Y-%m-%d")

    conf = obtener_config(db)
    turno_actual = conf.turno_activo
    estado_club = conf.estado_club

    fecha_f = fecha if fecha else fecha_actual
    turno_f = turno if turno else turno_actual

    productos_db = db.query(models.Producto).all()
    inv_productos = []
    inv_botellas = []
    
    for p in productos_db:
        inv_turno = db.query(models.InventarioTurno).filter(
            models.InventarioTurno.producto_id == p.id,
            models.InventarioTurno.fecha == fecha_f,
            models.InventarioTurno.turno == turno_f
        ).first()
        
        inicio_stock = inv_turno.inicio if inv_turno else p.inicio
        
        # 1. SALIDAS
        salida = db.query(func.count(models.Venta.id)).filter(
            models.Venta.producto_id == p.id,
            func.strftime("%Y-%m-%d", models.Venta.fecha) == fecha_f,
            models.Venta.turno == turno_f
        ).scalar() or 0
        
        # 2. REPOSICIONES
        repos = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
            models.StockMovimiento.producto_id == p.id,
            models.StockMovimiento.tipo_movimiento == 'REPOSICION',
            models.StockMovimiento.fecha == fecha_f,
            models.StockMovimiento.turno == turno_f
        ).scalar() or 0

        # 3. FALTANTES
        falts = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
            models.StockMovimiento.producto_id == p.id,
            models.StockMovimiento.tipo_movimiento == 'FALTANTE',
            models.StockMovimiento.fecha == fecha_f,
            models.StockMovimiento.turno == turno_f
        ).scalar() or 0

        saldo = (inicio_stock + repos) - salida - falts

        datos = {
            "id": p.id, "nombre": p.nombre, "reposicion": repos,
            "salida": salida, "faltante": falts, "saldo": saldo
        }
        if p.tipo == "PRODUCTO": inv_productos.append(datos)
        else: inv_botellas.append(datos)

    # Auditoría
    audit_db = db.query(models.StockMovimiento).filter(
        models.StockMovimiento.fecha == fecha_f,
        models.StockMovimiento.turno == turno_f
    ).order_by(models.StockMovimiento.id.desc()).all()
    
    auditoria = []
    for a in audit_db:
        nombre_p = a.nombre_respaldo if a.nombre_respaldo else (db.query(models.Producto.nombre).filter(models.Producto.id == a.producto_id).scalar() or "N/A")
        auditoria.append({
            "hora": a.hora,
            "nombre": nombre_p,
            "tipo": a.tipo_movimiento,
            "cantidad": a.cantidad,
            "usuario": a.usuario
        })

    return templates.TemplateResponse(request=request, name="stock.html", context={
        "inventario_productos": inv_productos, "inventario_botellas": inv_botellas,
        "auditoria": auditoria, "fecha_filtro": fecha_f, "turno_filtro": turno_f,
        "fecha_actual": fecha_actual, "turno_actual": turno_actual, "estado_club": estado_club
    })

# ---------------------------------------------------------
# 2. ACCIONES DE STOCK (CON BLOQUEO ANTIFRAUDE)
# ---------------------------------------------------------

@router.post("/agregar_producto_stock")
async def agregar_producto(request: Request, nombre: str = Form(...), tipo: str = Form(...), inicio: int = Form(...), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["jefe", "admin", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)

    nuevo = models.Producto(nombre=nombre.upper(), tipo=tipo, inicio=inicio)
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    
    conf = obtener_config(db)
    if conf.estado_club == "ABIERTO":
        ahora = obtener_ahora_local()
        if ahora.time() < time(6, 0):
            fecha_actual = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            fecha_actual = ahora.strftime("%Y-%m-%d")
        
        nuevo_inv = models.InventarioTurno(
            producto_id=nuevo.id,
            fecha=fecha_actual,
            turno=conf.turno_activo,
            inicio=inicio
        )
        db.add(nuevo_inv)
        db.commit()
        
    return RedirectResponse(url="/stock", status_code=303)

@router.post("/registrar_movimiento_stock")
async def registrar_mov(
    request: Request,
    producto_id: int = Form(...), 
    cantidad_repo: int = Form(0), 
    cantidad_falt: int = Form(0), 
    fecha: str = Form(...), 
    turno: str = Form(...), 
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["jefe", "admin", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)

    # 🔒 VALIDACIÓN DE SEGURIDAD ANTIFRAUDE (SERVER-SIDE)
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_real = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_real = ahora.strftime("%Y-%m-%d")
        
    conf = obtener_config(db)
    
    # Bloqueamos cualquier intento de meter datos en días pasados, futuros o turnos inactivos
    if fecha != fecha_real or turno != conf.turno_activo or conf.estado_club != "ABIERTO":
        return RedirectResponse(url=f"/stock?fecha={fecha_real}&turno={conf.turno_activo}", status_code=303)

    hora = ahora.strftime("%H:%M")
    
    if cantidad_repo > 0:
        mov = models.StockMovimiento(
            producto_id=producto_id, 
            tipo_movimiento='REPOSICION', 
            cantidad=cantidad_repo, 
            usuario=username,
            fecha=fecha, 
            turno=turno, 
            hora=hora
        )
        db.add(mov)
    
    if cantidad_falt > 0:
        mov = models.StockMovimiento(
            producto_id=producto_id, 
            tipo_movimiento='FALTANTE', 
            cantidad=cantidad_falt, 
            usuario=username,
            fecha=fecha, 
            turno=turno, 
            hora=hora
        )
        db.add(mov)
    
    db.commit()
    return RedirectResponse(url=f"/stock?fecha={fecha}&turno={turno}", status_code=303)

@router.post("/eliminar_producto/{id}")
async def eliminar_prod(request: Request, id: int, fecha: str = Form(...), turno: str = Form(...), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["jefe", "admin", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)

    # 🔒 VALIDACIÓN DE SEGURIDAD ANTIFRAUDE (SERVER-SIDE)
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_real = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_real = ahora.strftime("%Y-%m-%d")
        
    conf = obtener_config(db)
    
    if fecha != fecha_real or turno != conf.turno_activo or conf.estado_club != "ABIERTO":
        return RedirectResponse(url=f"/stock?fecha={fecha_real}&turno={conf.turno_activo}", status_code=303)

    p = db.query(models.Producto).filter(models.Producto.id == id).first()
    if p:
        ahora_hora = ahora.strftime("%H:%M")
        mov = models.StockMovimiento(
            producto_id=None, 
            nombre_respaldo=p.nombre, 
            tipo_movimiento='ELIMINADO',
            cantidad=0, 
            usuario=username, 
            fecha=fecha, 
            turno=turno, 
            hora=ahora_hora
        )
        db.add(mov)
        db.delete(p)
        db.commit()
    return RedirectResponse(url=f"/stock?fecha={fecha}&turno={turno}", status_code=303)