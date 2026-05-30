# routes/stock.py

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera", "jefe_guillermo", "encargado"]:
        return RedirectResponse(url="/", status_code=303)

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

    # 💡 Obtenemos solo los licores y productos activos de este turno específico
    productos_db = db.query(models.Producto).filter(
        models.Producto.es_corto == False,
        models.Producto.turno == turno_f  # Filtrado por turno seleccionado
    ).all()

    inv_productos = []
    inv_botellas = []
    
    for p in productos_db:
        inv_turno = db.query(models.InventarioTurno).filter(
            models.InventarioTurno.producto_id == p.id,
            models.InventarioTurno.fecha == fecha_f,
            models.InventarioTurno.turno == turno_f
        ).first()
        
        if inv_turno:
            inicio_stock = inv_turno.inicio
        else:
            # Búsqueda del último estado del producto filtrando estrictamente por el mismo nombre de turno
            ultimo_registro = db.query(models.InventarioTurno).filter(
                models.InventarioTurno.producto_id == p.id,
                models.InventarioTurno.turno == turno_f
            ).order_by(
                models.InventarioTurno.fecha.desc(), 
                models.InventarioTurno.id.desc()
            ).first()
            
            if ultimo_registro:
                inicio_stock = ultimo_registro.inicio
            else:
                inicio_stock = 0
        
        # Movimientos y consumos en el turno activo
        salida_directa = db.query(func.count(models.Venta.id)).filter(
            models.Venta.producto_id == p.id,
            models.Venta.fecha_operativa == fecha_f,
            models.Venta.turno == turno_f
        ).scalar() or 0
        
        repos = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
            models.StockMovimiento.producto_id == p.id,
            models.StockMovimiento.tipo_movimiento == 'REPOSICION',
            models.StockMovimiento.fecha == fecha_f,
            models.StockMovimiento.turno == turno_f
        ).scalar() or 0

        falts = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
            models.StockMovimiento.producto_id == p.id,
            models.StockMovimiento.tipo_movimiento.in_(['FALTANTE', 'APERTURA BOTELLA']),
            models.StockMovimiento.fecha == fecha_f,
            models.StockMovimiento.turno == turno_f
        ).scalar() or 0

        botellas_debitadas_por_cortos = 0
        cortos_sueltos_restantes = 0
        
        es_sellada = False
        if p.tipo == "BOTELLA" and p.capacidad_cortos:
            marca_base = p.nombre.split(" ")[0]
            corto_vinculado = db.query(models.Producto).filter(
                models.Producto.nombre == f"CORTO {marca_base}",
                models.Producto.turno == turno_f
            ).first()
            
            if corto_vinculado:
                if corto_vinculado.parent_botella_id != p.id:
                    es_sellada = True
                else:
                    cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
                        models.Venta.producto_id == corto_vinculado.id,
                        models.Venta.fecha_operativa == fecha_f,
                        models.Venta.turno == turno_f
                    ).scalar() or 0
                    
                    botellas_debitadas_por_cortos = cortos_consumidos // p.capacidad_cortos
                    cortos_sueltos_restantes = cortos_consumidos % p.capacidad_cortos

        salida_total = salida_directa + botellas_debitadas_por_cortos
        saldo = (inicio_stock + repos) - salida_total - falts

        saldo_visual = f"{saldo}"
        if es_sellada:
            saldo_visual = f"{saldo} (🔒 SELLADA)"
        elif cortos_sueltos_restantes > 0 and saldo > 0:
            saldo_visual = f"{saldo} (Abierta: {cortos_sueltos_restantes} de {p.capacidad_cortos} cortos)"

        salida_visual = f"{salida_directa}"
        if botellas_debitadas_por_cortos > 0:
            salida_visual = f"{salida_directa} + {botellas_debitadas_por_cortos} (Cortos)"

        datos = {
            "id": p.id, 
            "nombre": p.nombre, 
            "inicio_stock": inicio_stock,  
            "reposicion": repos,
            "salida": salida_total, 
            "salida_visual": salida_visual, 
            "faltante": falts, 
            "saldo": saldo,
            "saldo_visual": saldo_visual
        }
        if p.tipo == "PRODUCTO": 
            inv_productos.append(datos)
        else: 
            inv_botellas.append(datos)

    audit_db = db.query(models.StockMovimiento).filter(
        models.StockMovimiento.fecha == fecha_f,
        models.StockMovimiento.turno == turno_f
    ).order_by(models.StockMovimiento.id.desc()).all()
    
    auditoria = []
    for a in audit_db:
        nombre_p = a.nombre_respaldo if a.nombre_respaldo else (db.query(models.Producto.nombre).filter(models.Producto.id == a.producto_id).scalar() or "N/A")
        auditoria.append({
            "hora": a.hora, "nombre": nombre_p, "tipo": a.tipo_movimiento,
            "cantidad": a.cantidad, "usuario": a.usuario
        })

    # 💡 CORRECCIÓN DE CLAVES CONTEXTUALES: Alineado con stock.html
    return templates.TemplateResponse(request=request, name="stock.html", context={
        "inv_productos": inv_productos, 
        "inv_botellas": inv_botellas,
        "auditoria": auditoria, 
        "fecha_filtro": fecha_f, 
        "turno_filtro": turno_f, 
        "fecha_actual": fecha_actual, 
        "turno_actual": turno_actual, 
        "estado_club": estado_club,
        "role": user_role  
    })

# ---------------------------------------------------------
# 2. ACCIONES DE STOCK (CON CREACIÓN AUTOMÁTICA DE CORTOS)
# ---------------------------------------------------------
from typing import Optional

@router.post("/agregar_producto_stock")
async def agregar_producto(
    request: Request, 
    nombre: str = Form(...), 
    tipo: str = Form(...), 
    inicio: int = Form(...), 
    volumen: Optional[str] = Form(None),
    vende_cortos: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)

    nombre_original_limpio = nombre.strip().upper()
    
    # Construcción de nombre según volumen
    if tipo == "BOTELLA" and volumen:
        nombre_sistema = f"{nombre_original_limpio} {volumen}CC"
        mapeo = {"750": 10, "1000": 13, "1500": 20, "2000": 26}
        capacidad = mapeo.get(volumen, 10)
    else:
        nombre_sistema = nombre_original_limpio
        capacidad = None

    conf = obtener_config(db)
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_actual = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_actual = ahora.strftime("%Y-%m-%d")

    try:
        # 1. 💡 Verificar si el producto ya existe en el catálogo de este turno específico
        existe_producto_global = db.query(models.Producto).filter(
            models.Producto.nombre == nombre_sistema,
            models.Producto.turno == conf.turno_activo
        ).first()

        if existe_producto_global:
            return RedirectResponse(url="/stock?error=producto_ya_existe_en_este_turno", status_code=303)

        # Caso B: El producto es nuevo en este turno
        nuevo = models.Producto(
            nombre=nombre_sistema, 
            tipo=tipo, 
            inicio=inicio,
            capacidad_cortos=capacidad,
            turno=conf.turno_activo  # 💡 Almacenamos el turno activo
        )
        db.add(nuevo)
        db.flush() 

        # Crear el corto si es botella y se solicita
        corto_db = None
        if tipo == "BOTELLA" and vende_cortos == "on":
            nombre_corto = f"CORTO {nombre_original_limpio}"
            corto_db = db.query(models.Producto).filter(
                models.Producto.nombre == nombre_corto,
                models.Producto.turno == conf.turno_activo
            ).first()
            if not corto_db:
                corto_db = models.Producto(
                    nombre=nombre_corto,
                    tipo="PRODUCTO",
                    inicio=0,
                    es_corto=True,
                    parent_botella_id=nuevo.id,
                    turno=conf.turno_activo  # 💡 Almacenamos el turno activo
                )
                db.add(corto_db)
                db.flush()

        nuevo_inv = models.InventarioTurno(
            producto_id=nuevo.id,
            fecha=fecha_actual,
            turno=conf.turno_activo,
            inicio=inicio
        )
        db.add(nuevo_inv)

        if corto_db:
            nuevo_inv_corto = models.InventarioTurno(
                producto_id=corto_db.id,
                fecha=fecha_actual,
                turno=conf.turno_activo,
                inicio=0
            )
            db.add(nuevo_inv_corto)

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"❌ Error al registrar o enlazar el producto por turnos aislados: {str(e)}")
        return RedirectResponse(url="/stock?error=error_transaccion", status_code=303)

    return RedirectResponse(url="/stock", status_code=303)

@router.post("/registrar_movimiento_stock")
async def registrar_mov(
    request: Request,
    producto_id: int = Form(...), 
    offset_repo: int = Form(0, alias="cantidad_repo"), 
    offset_falt: int = Form(0, alias="cantidad_falt"), 
    fecha: str = Form(...), 
    turno: str = Form(...), 
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)
    
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_real = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_real = ahora.strftime("%Y-%m-%d")
        
    conf = obtener_config(db)
    
    if fecha != fecha_real or turno != conf.turno_activo or conf.estado_club != "ABIERTO":
        return RedirectResponse(url=f"/stock?fecha={fecha_real}&turno={conf.turno_activo}", status_code=303)

    hora = ahora.strftime("%H:%M")
    
    if offset_repo > 0:
        mov = models.StockMovimiento(
            producto_id=producto_id, 
            tipo_movimiento='REPOSICION', 
            cantidad=offset_repo, 
            usuario=username,
            fecha=fecha, 
            turno=turno, 
            hora=hora
        )
        db.add(mov)
    
    if offset_falt > 0:
        mov = models.StockMovimiento(
            producto_id=producto_id, 
            tipo_movimiento='FALTANTE', 
            cantidad=offset_falt, 
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
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/stock", status_code=303)

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

# ---------------------------------------------------------
# 🔒 3. API DE VERIFICACIÓN Y APERTURA DE RESPALDO (NUEVO)
# ---------------------------------------------------------
@router.get("/api/estado_corto/{corto_id}")
async def estado_corto(corto_id: int, db: Session = Depends(get_db)):
    corto = db.query(models.Producto).filter(models.Producto.id == corto_id).first()
    if not corto or not corto.parent_botella_id:
        return {"vacia": False, "respaldos": []}
    
    padre = db.query(models.Producto).filter(models.Producto.id == corto.parent_botella_id).first()
    if not padre:
        return {"vacia": False, "respaldos": []}
        
    conf = obtener_config(db)
    ahora = obtener_ahora_local()
    fecha_hoy = ahora.strftime("%Y-%m-%d") if ahora.time() >= time(6, 0) else (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    
    inv_turno = db.query(models.InventarioTurno).filter(
        models.InventarioTurno.producto_id == padre.id,
        models.InventarioTurno.fecha == fecha_hoy,
        models.InventarioTurno.turno == conf.turno_activo
    ).first()
    
    inicio = inv_turno.inicio if inv_turno else padre.inicio
    salidas = db.query(func.count(models.Venta.id)).filter(
        models.Venta.producto_id == padre.id,
        models.Venta.fecha_operativa == fecha_hoy,
        models.Venta.turno == conf.turno_activo
    ).scalar() or 0
    
    cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
        models.Venta.producto_id == corto.id,
        models.Venta.fecha_operativa == fecha_hoy,
        models.Venta.turno == conf.turno_activo
    ).scalar() or 0
    
    botellas_debitadas = cortos_consumidos // padre.capacidad_cortos
    saldo = inicio - (salidas + botellas_debitadas)
    
    vacia = (saldo <= 0)
    
    respaldos = []
    if vacia:
        marca_base = padre.nombre.split(" ")[0]
        respaldos_db = db.query(models.Producto).filter(
            models.Producto.tipo == "BOTELLA",
            models.Producto.nombre.like(f"{marca_base}%"),
            models.Producto.id != padre.id
        ).all()
        
        for r in respaldos_db:
            respaldos.append({"id": r.id, "nombre": r.nombre})
            
    return {"vacia": vacia, "parent_nombre": padre.nombre, "respaldos": respaldos}


@router.post("/api/abrir_botella_respaldo")
async def abrir_botella_respaldo(
    request: Request,
    corto_id: int = Form(...),
    nueva_botella_id: int = Form(...),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if user_role not in ["admin1", "administrador", "cajera"]:
        return JSONResponse(status_code=403, content={"status": "error", "message": "No autorizado"})
        
    corto = db.query(models.Producto).filter(models.Producto.id == corto_id).first()
    nueva_botella = db.query(models.Producto).filter(models.Producto.id == nueva_botella_id).first()
    
    if corto and nueva_botella:
        corto.parent_botella_id = nueva_botella.id
        
        ahora = obtener_ahora_local()
        fecha_hoy = ahora.strftime("%Y-%m-%d") if ahora.time() >= time(6, 0) else (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
        conf = obtener_config(db)
        
        log_mov = models.StockMovimiento(
            producto_id=nueva_botella.id,
            tipo_movimiento="APERTURA BOTELLA",
            cantidad=1,
            usuario=username,
            fecha=fecha_hoy,
            turno=conf.turno_activo,
            hora=ahora.strftime("%H:%M")
        )
        db.add(log_mov)
        db.commit()
        return {"status": "success", "parent_nombre": nueva_botella.nombre}
        
    return JSONResponse(status_code=400, content={"status": "error", "message": "Productos inválidos"})