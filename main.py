# =========================================================
# 1. IMPORTACIONES DEL SISTEMA (Librerías externas)
# =========================================================
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime, time, timedelta
import uvicorn
import urllib.parse

# =========================================================
# 2. IMPORTACIONES LOCALES (Base de Datos, Modelos y Servicios)
# =========================================================
from database import SessionLocal, engine, get_db
import models
from routes import ventas, asistencia, damas, garzones as garzones_routes, stock, auth, usuarios
from services.config_service import obtener_config

# Importaciones de seguridad y tiempo local
from services.auth_service import obtener_usuario_sesion
from services.time_service import obtener_ahora_local

# =========================================================
# 3. FUNCIONES DE AYUDA (Fecha Operativa, Sesión y Stock)
# =========================================================

def obtener_fecha_operativa() -> datetime:
    """
    Retorna la fecha operativa real del local. 
    Si son entre las 00:00 AM y las 06:00 AM, contablemente todavía es el día anterior.
    """
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):  # Si es de madrugada (antes de las 6:00 AM)
        return ahora - timedelta(days=1)
    return ahora

async def verificar_sesion(request: Request):
    """Verificador de sesión seguro con firmas criptográficas."""
    user, _ = obtener_usuario_sesion(request)
    if not user:
        raise HTTPException(status_code=303, detail="No autorizado")
    return user

def inicializar_stock_nuevo_turno(db: Session, fecha_hoy: str, turno_nuevo: str):
    existe = db.query(models.InventarioTurno).filter(
        models.InventarioTurno.fecha == fecha_hoy,
        models.InventarioTurno.turno == turno_nuevo
    ).first()
    
    if existe:
        return

    productos = db.query(models.Producto).all()
    for p in productos:
        ultimo_registro = db.query(models.InventarioTurno).filter(
            models.InventarioTurno.producto_id == p.id
        ).order_by(models.InventarioTurno.fecha.desc(), models.InventarioTurno.id.desc()).first()
        
        stock_inicial = p.inicio
        
        if ultimo_registro:
            # 1. Salidas directas de botellas enteras o productos
            salida_directa = db.query(func.count(models.Venta.id)).filter(
                models.Venta.producto_id == p.id,
                func.strftime("%Y-%m-%d", models.Venta.fecha) == ultimo_registro.fecha,
                models.Venta.turno == ultimo_registro.turno
            ).scalar() or 0
            
            # 2. Descuento proporcional de cortos para arrastrar el stock correcto
            botellas_debitadas_por_cortos = 0
            if p.tipo == "BOTELLA" and p.capacidad_cortos:
                corto_vinculado = db.query(models.Producto).filter(models.Producto.parent_botella_id == p.id).first()
                if corto_vinculado:
                    cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
                        models.Venta.producto_id == corto_vinculado.id,
                        func.strftime("%Y-%m-%d", models.Venta.fecha) == ultimo_registro.fecha,
                        models.Venta.turno == ultimo_registro.turno
                    ).scalar() or 0
                    botellas_debitadas_por_cortos = cortos_consumidos // p.capacidad_cortos
            
            salida_total = salida_directa + botellas_debitadas_por_cortos
            
            repos = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
                models.StockMovimiento.producto_id == p.id,
                models.StockMovimiento.tipo_movimiento == 'REPOSICION',
                models.StockMovimiento.fecha == ultimo_registro.fecha,
                models.StockMovimiento.turno == ultimo_registro.turno
            ).scalar() or 0

            falts = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
                models.StockMovimiento.producto_id == p.id,
                models.StockMovimiento.tipo_movimiento == 'FALTANTE',
                models.StockMovimiento.fecha == ultimo_registro.fecha,
                models.StockMovimiento.turno == ultimo_registro.turno
            ).scalar() or 0
            
            saldo_final = (ultimo_registro.inicio + repos) - salida_total - falts
            stock_inicial = max(0, saldo_final)
            
        nuevo_inventario = models.InventarioTurno(
            producto_id=p.id,
            fecha=fecha_hoy,
            turno=turno_nuevo,
            inicio=stock_inicial
        )
        db.add(nuevo_inventario)
        
    db.commit()
# =========================================================
# 4. CONFIGURACIÓN E INICIALIZACIÓN DE LA APLICACIÓN
# =========================================================

# Crear las tablas de la base de datos al iniciar si no existen
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# Configuración de archivos estáticos y plantillas
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Registro de Routers (Rutas externas)
app.include_router(ventas.router)
app.include_router(asistencia.router)
app.include_router(damas.router)
app.include_router(garzones_routes.router)
app.include_router(stock.router)
app.include_router(auth.router)
app.include_router(usuarios.router)

# ---------------------------------------------------------
# RUTA PRINCIPAL (DASHBOARD)
# ---------------------------------------------------------
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    # 🔒 1. VERIFICACIÓN DE SEGURIDAD SEGURA CONTRA HACKERS (Verifica firma digital)
    username, user_role = obtener_usuario_sesion(request)

    # Si las cookies no existen o la firma criptográfica fue alterada por un hacker:
    if not username or not user_role:
        return RedirectResponse(url="/login", status_code=303)
    
    # 2. OBTENER CONFIGURACIÓN DEL CLUB
    conf = obtener_config(db)
    
    # --- FECHA OPERATIVA NOCTURNA Y LOCAL ---
    hoy_dt = obtener_fecha_operativa()  #  CORREGIDO: Usa la hora local y la jornada nocturna
    # ----------------------------------------
    
    inicio_dia = datetime.combine(hoy_dt.date(), time.min)
    fin_dia = datetime.combine(hoy_dt.date(), time.max)
    fecha_hoy_str = hoy_dt.strftime("%Y-%m-%d")
    
    # 3. SUMAR VENTAS DEL TURNO ACTIVO
    total_ventas = db.query(func.sum(models.Venta.monto)).filter(
        models.Venta.fecha >= inicio_dia,
        models.Venta.fecha <= fin_dia,
        models.Venta.turno == conf.turno_activo
    ).scalar() or 0.0
    
    # 4. SUMAR DEUDAS
    total_deudas = db.query(func.sum(models.Venta.monto)).filter(
        models.Venta.metodo_pago == "CUENTA"
    ).scalar() or 0.0
    
    # 5. OBTENER ASISTENCIAS
    asistencias = db.query(models.Asistencia).filter(
        models.Asistencia.fecha == fecha_hoy_str,
        models.Asistencia.turno == conf.turno_activo
    ).all()
    
    ids_presentes = [a.dama_id for a in asistencias]
    dict_asistencias = {a.dama_id: a for a in asistencias}
    
    # 6. PREPARAR DAMAS PARA EL SALÓN
    damas_pantalla = []
    if conf.estado_club == "ABIERTO":
        damas_db = db.query(models.Dama).filter(models.Dama.id.in_(ids_presentes)).all()
        for d in damas_db:
            asis = dict_asistencias.get(d.id)
            damas_pantalla.append({
                "id": d.id, 
                "nombre_artistico": d.nombre_artistico, 
                "foto_url": d.foto_url,
                "bailando_hoy": asis.bailando_hoy if asis else False
            })
    
    # 7. LÓGICA DE BAILARINAS DIVIDIDA (LA CORRECCIÓN ESTÁ AQUÍ)
    bailando_hoy = []
    en_espera = []
    ausentes_b = []

    if conf.turno_activo == "Turno 1":
        todas_b = db.query(models.Dama).filter(
            models.Dama.es_bailarina == True, 
            models.Dama.esta_activa == True
        ).all()

        for b in todas_b:
            asis_hoy = dict_asistencias.get(b.id)
            info = {
                "id": b.id,
                "nombre": b.nombre_artistico,
                "foto_url": b.foto_url,
                "presente": b.id in ids_presentes, 
                "asistencia_id": asis_hoy.id if asis_hoy else None,
                "monto_shows": asis_hoy.bono_show if asis_hoy else 0,
                "bailando": asis_hoy.bailando_hoy if asis_hoy else False
            }
            
            if not info["presente"]:
                ausentes_b.append(info)
            elif info["bailando"] or info["monto_shows"] > 0:
                bailando_hoy.append(info)
            else:
                en_espera.append(info)

    # 8. META DIARIA
    meta_fija = 4000000.0
    porcentaje = (total_ventas / meta_fija) * 100 if meta_fija > 0 else 0
    if porcentaje > 100: 
        porcentaje = 100

    # 9. RETORNO DE LA RESPUESTA (ACTUALIZADO CON LAS NUEVAS LISTAS)
    return templates.TemplateResponse(
        request=request,
        name="index.html", 
        context={
            "username": username,
            "role": user_role,
            "estado_club": conf.estado_club,
            "turno": conf.turno_activo,
            "actual": total_ventas,
            "meta": conf.meta_diaria,
            "porcentaje": porcentaje,
            "total_deudas": total_deudas,
            "damas": damas_pantalla,
            "bailando_hoy": bailando_hoy,   # <--- Cambio
            "en_espera": en_espera,         # <--- Cambio
            "ausentes_b": ausentes_b,       # <--- Cambio
            "dict_bailando": {a.dama_id: a.bailando_hoy for a in asistencias},
            "garzones": db.query(models.Mesero).all(),
            "productos_cooler": db.query(models.Producto).filter(models.Producto.tipo == "PRODUCTO").all(),
            "productos_todos": db.query(models.Producto).all()
        }
    )

# ---------------------------------------------------------
# GESTIÓN DE TURNO (ABRIR/CERRAR) - CORREGIDO
# ---------------------------------------------------------
# ---------------------------------------------------------
# GESTIÓN DE TURNO (ABRIR/CERRAR) - UNIFICADO Y SEGURO
# ---------------------------------------------------------
@app.post("/gestionar_club")
async def gestionar_club(request: Request, accion: str = Form(...), turno: str = Form(None), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if user_role not in ["admin1", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    # 2. CONFIGURACIÓN DEL CLUB (Inicialización si no existe)
    conf = db.query(models.Configuracion).first()
    if not conf:
        conf = models.Configuracion(estado_club="CERRADO", turno_activo="Turno 1", meta_diaria=3000000.0)
        db.add(conf)
    
    accion_limpia = accion.upper()
    
    # 3. LÓGICA DE APERTURA / CIERRE
    if accion_limpia == "ABRIR":
        conf.estado_club = "ABIERTO"
        if turno:
            conf.turno_activo = turno
            
            # 📦 CONGELACIÓN TRANSACCIONAL DE INVENTARIO
            # Usamos la función obtener_fecha_operativa() que ya declaramos al inicio de main.py
            fecha_op = obtener_fecha_operativa().strftime("%Y-%m-%d")
            inicializar_stock_nuevo_turno(db, fecha_op, turno)
            
    else:
        conf.estado_club = "CERRADO"

    # 4. AUDITORÍA (Registramos el username real que hizo la acción)
    log = models.LogAuditoria(
        usuario=username, 
        accion=f"{accion_limpia} CLUB - {conf.turno_activo}",
        turno=conf.turno_activo  # 💡 <-- AGREGAR ESTA LÍNEA
    )
    db.add(log)
    
    # 5. GUARDAR Y REFRESCAR
    db.commit()
    db.refresh(conf) 
    
    # 6. REDIRECCIÓN LIMPIA 
    return RedirectResponse(url="/", status_code=303)

# ---------------------------------------------------------
# CONTABILIDAD Y REPORTES
# ---------------------------------------------------------

@app.get("/contabilidad", response_class=HTMLResponse)  
async def contabilidad_page(request: Request, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)

    # Permite ver a los dueños, administradores y cajeras
    if not username or user_role not in ["admin1", "cajera", "jefe_guillermo", "admin2"]:
        return RedirectResponse(url="/login?error=no_autorizado", status_code=303)
    
    # --- LÓGICA DE FILTROS SEGURA ---
    hoy_dt = obtener_fecha_operativa()
    fecha_param = request.query_params.get("fecha", hoy_dt.strftime("%Y-%m-%d"))
    turno_filter = request.query_params.get("turno", "Turno 1")
    
    # Filtro de fecha para la base de datos (todo el día de 00:00 a 23:59)
    fecha_obj = datetime.strptime(fecha_param, "%Y-%m-%d")
    inicio_dia = datetime.combine(fecha_obj.date(), time.min)
    fin_dia = datetime.combine(fecha_obj.date(), time.max)

    # 1. Obtener Ventas del turno
    ventas_hoy = db.query(models.Venta).filter(
        models.Venta.fecha >= inicio_dia, 
        models.Venta.fecha <= fin_dia, 
        models.Venta.turno == turno_filter
    ).all()
    
    # 2. Obtener Asistencias del turno
    asistencias_hoy = db.query(models.Asistencia).filter(
        models.Asistencia.fecha == fecha_param, 
        models.Asistencia.turno == turno_filter
    ).all()

    # 3. Resumen General
    resumen = {
        "bruto": sum(v.monto for v in ventas_hoy),
        "efectivo": sum(v.monto for v in ventas_hoy if v.metodo_pago == "EFECTIVO"),
        "tarjeta": sum(v.monto for v in ventas_hoy if v.metodo_pago == "TARJETA"),
        "transferencia": sum(v.monto for v in ventas_hoy if v.metodo_pago == "TRANSFERENCIA"),
    }

    # 4. Cálculo detallado por Dama (Liquidaciones)
    # 4. Cálculo detallado por Dama (Liquidaciones en múltiples Fichas)
    detalle_damas = []
    for asis in asistencias_hoy:
        dama = db.query(models.Dama).filter(models.Dama.id == asis.dama_id).first()
        if not dama: continue
        
        # Filtramos ventas de hoy asociadas a la dama según su estado de liquidación
        ventas_pagadas = [v for v in ventas_hoy if v.dama_id == dama.id and v.liquidada]
        ventas_pendientes = [v for v in ventas_hoy if v.dama_id == dama.id and not v.liquidada]

        # 💡 Cálculo de Bonos Base del turno (Bono de asistencia, shows y descuento residencia)
        monto_bono_base = 0
        costo_residencia_base = 0
        if asis.turno == "Turno 1":
            if (asis.tipo_llegada == "Residente" and asis.hora_libro <= "22:35") or \
               (asis.tipo_llegada == "Externa" and asis.hora_libro <= "23:05"):
                monto_bono_base = 10000
        if asis.tipo_llegada == "Residente":
            costo_residencia_base = 5000

        whatsapp_limpio = "".join(c for c in dama.whatsapp if c.isdigit()) if dama.whatsapp else ""

        # CASO A: Si ya se pagó la Ficha 1 (asis.liquidada == True)
        if asis.liquidada:
            # 1. Añadimos Ficha 1 ya PAGADA (Tragos pagados + todos los bonos/shows/residencia)
            total_comis_pagadas = sum(v.comision_chica for v in ventas_pagadas)
            total_ficha1 = (total_comis_pagadas + monto_bono_base + asis.bono_show) - costo_residencia_base
            
            tragos_det_f1 = "".join(f"• {v.fecha.strftime('%H:%M')} - {v.servicio} (+${v.comision_chica:,.0f})\n" for v in ventas_pagadas)
            if not tragos_det_f1: tragos_det_f1 = "• Sin consumos en esta ficha.\n"

            msg_f1 = (
                f"⭐ *DETALLE DE LIQUIDACIÓN - {dama.nombre_artistico} (FICHA 1)* ⭐\n"
                f"📅 *Fecha:* {asis.fecha} | 🕒 *Turno:* {asis.turno}\n"
                f"-----------------------------------------\n"
                f"💼 *COMISIONES DE TRAGOS:*\n{tragos_det_f1}"
                f"-----------------------------------------\n"
                f"➕ *BONOS / ADICIONALES:*\n"
                f"• Bono de Asistencia: +${monto_bono_base:,.0f}\n"
            )
            if dama.es_bailarina:
                msg_f1 += f"• Ganancia de Bailes: +${asis.bono_show:,.0f}\n"
            msg_f1 += (
                f"-----------------------------------------\n"
                f"➖ *DESCUENTOS:*\n"
                f"• Descuento Residencia: -${costo_residencia_base:,.0f}\n"
                f"-----------------------------------------\n"
                f"💵 *TOTAL NETO RECIBIDO:* *${total_ficha1:,.0f}*\n"
                f"-----------------------------------------\n"
                f"_Ficha cerrada y pagada con éxito._ ✔"
            )

            detalle_damas.append({
                "dama_id": dama.id,
                "nombre": f"{dama.nombre_artistico} (FICHA 1)",
                "fecha": asis.fecha,
                "total_pagar": total_ficha1,
                "monto_bono": monto_bono_base,
                "residencia": costo_residencia_base,
                "ganancia_bailes": asis.bono_show,
                "es_bailarina": dama.es_bailarina,
                "consumos": [{"hora": v.fecha.strftime("%H:%M"), "serv": v.servicio, "ganancia": v.comision_chica, "garzon": v.mesero} for v in ventas_pagadas],
                "link_wa": f"https://wa.me/{whatsapp_limpio}?text={urllib.parse.quote(msg_f1)}",
                "liquidada": True
            })

            # 2. Si hay nuevos tragos no pagados, abrimos de forma automática la FICHA 2 (PENDIENTE)
            if len(ventas_pendientes) > 0:
                total_ficha2 = sum(v.comision_chica for v in ventas_pendientes)
                tragos_det_f2 = "".join(f"• {v.fecha.strftime('%H:%M')} - {v.servicio} (+${v.comision_chica:,.0f})\n" for v in ventas_pendientes)

                msg_f2 = (
                    f"⭐ *DETALLE DE LIQUIDACIÓN - {dama.nombre_artistico} (FICHA 2)* ⭐\n"
                    f"📅 *Fecha:* {asis.fecha} | 🕒 *Turno:* {asis.turno}\n"
                    f"--------------------------------\n"
                    f"💼 *COMISIONES DE TRAGOS:*\n{tragos_det_f2}"
                    f"--------------------------------\n"
                    f"➕ *BONOS / ADICIONALES:*\n"
                    f"• Bono de Asistencia: +$0 (Ya pagado en Ficha 1)\n"
                )
                if dama.es_bailarina:
                    msg_f2 += f"• Ganancia de Bailes: +$0 (Ya pagado en Ficha 1)\n"
                msg_f2 += (
                    f"--------------------------------\n"
                    f"➖ *DESCUENTOS:*\n"
                    f"• Descuento Residencia: -$0 (Ya deducido en Ficha 1)\n"
                    f"--------------------------------\n"
                    f"💵 *TOTAL NETO PENDIENTE (FICHA 2):* *${total_ficha2:,.0f}*\n"
                    f"--------------------------------\n"
                    f"_¡Muchas gracias por tu trabajo de hoy!_ 🌸"
                )

                detalle_damas.append({
                    "dama_id": dama.id,
                    "nombre": f"{dama.nombre_artistico} (FICHA 2)",
                    "fecha": asis.fecha,
                    "total_pagar": total_ficha2,
                    "monto_bono": 0,
                    "residencia": 0,
                    "ganancia_bailes": 0,
                    "es_bailarina": dama.es_bailarina,
                    "consumos": [{"hora": v.fecha.strftime("%H:%M"), "serv": v.servicio, "ganancia": v.comision_chica, "garzon": v.mesero} for v in ventas_pendientes],
                    "link_wa": f"https://wa.me/{whatsapp_limpio}?text={urllib.parse.quote(msg_f2)}",
                    "liquidada": False
                })

        # CASO B: Si la asistencia aún no ha sido liquidada (Ficha 1 Pendiente)
        else:
            total_comis_pendientes = sum(v.comision_chica for v in ventas_pendientes)
            total_ficha1_pend = (total_comis_pendientes + monto_bono_base + asis.bono_show) - costo_residencia_base
            
            tragos_det_f1_pend = "".join(f"• {v.fecha.strftime('%H:%M')} - {v.servicio} (+${v.comision_chica:,.0f})\n" for v in ventas_pendientes)
            if not tragos_det_f1_pend: tragos_det_f1_pend = "• Sin consumos registrados en este turno.\n"

            msg_f1_pend = (
                f"⭐ *DETALLE DE LIQUIDACIÓN - {dama.nombre_artistico} (FICHA 1)* ⭐\n"
                f"📅 *Fecha:* {asis.fecha} | 🕒 *Turno:* {asis.turno}\n"
                f"--------------------------------\n"
                f"💼 *COMISIONES DE TRAGOS:*\n{tragos_det_f1_pend}"
                f"--------------------------------\n"
                f"➕ *BONOS / ADICIONALES:*\n"
                f"• Bono de Asistencia: +${monto_bono_base:,.0f}\n"
            )
            if dama.es_bailarina:
                msg_f1_pend += f"• Ganancia de Bailes: +${asis.bono_show:,.0f}\n"
            msg_f1_pend += (
                f"--------------------------------\n"
                f"➖ *DESCUENTOS:*\n"
                f"• Descuento Residencia: -${costo_residencia_base:,.0f}\n"
                f"--------------------------------\n"
                f"💵 *TOTAL NETO PENDIENTE (FICHA 1):* *${total_ficha1_pend:,.0f}*\n"
                f"--------------------------------\n"
                f"_¡Muchas gracias por tu trabajo de hoy!_ 🌸"
            )

            detalle_damas.append({
                "dama_id": dama.id,
                "nombre": f"{dama.nombre_artistico} (FICHA 1)",
                "fecha": asis.fecha,
                "total_pagar": total_ficha1_pend,
                "monto_bono": monto_bono_base,
                "residencia": costo_residencia_base,
                "ganancia_bailes": asis.bono_show,
                "es_bailarina": dama.es_bailarina,
                "consumos": [{"hora": v.fecha.strftime("%H:%M"), "serv": v.servicio, "ganancia": v.comision_chica, "garzon": v.mesero} for v in ventas_pendientes],
                "link_wa": f"https://wa.me/{whatsapp_limpio}?text={urllib.parse.quote(msg_f1_pend)}",
                "liquidada": False
            })

    # 5. Cálculo detallado por Garzón
    damas_lookup = {d.id: d.nombre_artistico for d in db.query(models.Dama).all()}
    detalle_garzones = {}
    for v in ventas_hoy:
        if v.mesero not in detalle_garzones:
            detalle_garzones[v.mesero] = {"total": 0, "lista": []}
            
        # 💡 Resolvemos el destino correcto: Nombre de la dama, o el nombre del cliente
        if v.dama_id:
            destino = damas_lookup.get(v.dama_id, "S/D")
        else:
            destino = f"CLIENTE: {v.cliente_nombre}" if v.cliente_nombre else "CLIENTE SOLO"

        detalle_garzones[v.mesero]["total"] += v.monto
        detalle_garzones[v.mesero]["lista"].append({
            "id": v.id,  # 💡 <-- ID necesario para poder eliminar la venta
            "hora": v.fecha.strftime("%H:%M"),
            "servicio": v.servicio,
            "destino": destino,
            "monto": v.monto
        })

    # 6. Auditoría y Deudas Globales
    resumen["pagos_damas"] = sum(d["total_pagar"] for d in detalle_damas)
    resumen["neto"] = resumen["bruto"] - resumen["pagos_damas"]

    
    logs = db.query(models.LogAuditoria).filter(
        models.LogAuditoria.fecha >= inicio_dia, 
        models.LogAuditoria.fecha <= fin_dia,
        models.LogAuditoria.turno == turno_filter
    ).order_by(models.LogAuditoria.fecha.desc()).all()
    
    deudas_global = db.query(models.Venta).filter(models.Venta.metodo_pago == "CUENTA").all()

    # =========================================================================
    # 🔒 7. BÚSQUEDA GLOBAL DE LIQUIDACIONES PENDIENTES (OPTIMIZADA SIN N+1)
    # =========================================================================
    
    # A. Obtenemos todas las asistencias no liquidadas de la historia (Ficha 1 pendientes)
    asis_no_liq = db.query(models.Asistencia).filter(
        models.Asistencia.liquidada == False
    ).all()

    # B. Obtenemos todas las ventas no liquidadas de una sola vez
    ventas_pendientes_raw = db.query(models.Venta).filter(
        models.Venta.liquidada == False
    ).all()

    # Agrupamos las ventas en memoria por una clave única: (dama_id, fecha, turno)
    # Esto nos permite buscar ventas asociadas en tiempo récord O(1)
    ventas_agrupadas = {}
    claves_ventas_pendientes = set()
    for v in ventas_pendientes_raw:
        if not v.fecha:
            continue
        clave = (v.dama_id, v.fecha.strftime("%Y-%m-%d"), v.turno)
        if clave not in ventas_agrupadas:
            ventas_agrupadas[clave] = []
        ventas_agrupadas[clave].append(v)
        claves_ventas_pendientes.add((v.dama_id, v.fecha.strftime("%Y-%m-%d"), v.turno))

    # C. Obtenemos las asistencias ya pagadas (liquidada == True) pero que tienen ventas pendientes (Ficha 2+)
    asis_fichas_extras = []
    if claves_ventas_pendientes:
        from sqlalchemy import or_
        condiciones = [
            (models.Asistencia.dama_id == cid) & 
            (models.Asistencia.fecha == cfec) & 
            (models.Asistencia.turno == ctur)
            for cid, cfec, ctur in claves_ventas_pendientes
        ]
        if condiciones:
            asis_fichas_extras = db.query(models.Asistencia).filter(
                models.Asistencia.liquidada == True,
                or_(*condiciones)
            ).all()

    # D. Combinamos ambas listas de asistencias y ordenamos por fecha descendente
    asistencias_pendientes = asis_no_liq + asis_fichas_extras
    asistencias_pendientes.sort(key=lambda x: x.fecha, reverse=True)

    pendientes_global = []

    if asistencias_pendientes:
        # Obtenemos todas las Damas para indexarlas por ID
        damas_dict = {d.id: d for d in db.query(models.Dama).all()}

        # 🔄 Procesamos la lista unificada en memoria (Cero consultas adicionales dentro de este bucle)
        for asis_p in asistencias_pendientes:
            dama_p = damas_dict.get(asis_p.dama_id)
            if not dama_p: 
                continue
            
            # Buscamos las ventas del lote usando nuestra clave en memoria
            clave_asis = (asis_p.dama_id, asis_p.fecha, asis_p.turno)
            ventas_p = ventas_agrupadas.get(clave_asis, [])
            
            total_comis_p = sum(v.comision_chica for v in ventas_p)

            # Lógica dinámica: Si la asistencia ya está liquidada, es una Ficha 2
            if asis_p.liquidada:
                # Los bonos, shows iniciales y el descuento de residencia ya fueron aplicados en la Ficha 1
                monto_bono_p = 0
                costo_residencia_p = 0
                ganancia_bailes_p = 0
                nombre_ficha_label = f"{dama_p.nombre_artistico} (FICHA 2)"
            else:
                monto_bono_p = 0
                costo_residencia_p = 0
                if asis_p.turno == "Turno 1":
                    if (asis_p.tipo_llegada == "Residente" and asis_p.hora_libro <= "22:35") or \
                       (asis_p.tipo_llegada == "Externa" and asis_p.hora_libro <= "23:05"):
                        monto_bono_p = 10000
                if asis_p.tipo_llegada == "Residente":
                    costo_residencia_p = 5000
                ganancia_bailes_p = asis_p.bono_show
                nombre_ficha_label = f"{dama_p.nombre_artistico} (FICHA 1)"
                
            total_chica_p = (total_comis_p + monto_bono_p + ganancia_bailes_p) - costo_residencia_p

            if total_chica_p > 0:
                # Construcción del desglose
                tragos_detalle_p = ""
                for v_p in ventas_p:
                    tragos_detalle_p += f"• {v_p.fecha.strftime('%H:%M')} - {v_p.servicio} (+${v_p.comision_chica:,.0f}) [Garzón: {v_p.mesero}]\n"
                
                if not tragos_detalle_p:
                    tragos_detalle_p = "• Sin consumos.\n"

                # Mensaje personalizado según el estado de la ficha (Ficha 1 o Ficha 2)
                if asis_p.liquidada:
                    msg_p = (
                        f"⭐ *LIQUIDACIÓN PENDIENTE - {nombre_ficha_label}* ⭐\n"
                        f"📅 *Fecha:* {asis_p.fecha} | 🕒 *Turno:* {asis_p.turno}\n"
                        f"--------------------------------\n"
                        f"💼 *COMISIONES DE TRAGOS:*\n"
                        f"{tragos_detalle_p}"
                        f"--------------------------------\n"
                        f"➕ *BONOS:*\n"
                        f"• Bono de Asistencia: +$0 (Ya pagado en Ficha 1)\n"
                        f"--------------------------------\n"
                        f"➖ *DESCUENTOS:*\n"
                        f"• Descuento Residencia: -$0 (Ya deducido en Ficha 1)\n"
                        f"--------------------------------\n"
                        f"💵 *TOTAL NETO A RECIBIR (FICHA 2):*\n"
                        f"👉 *${total_chica_p:,.0f}*\n"
                        f"--------------------------------\n"
                        f"_¡Muchas gracias!_ 🌸"
                    )
                else:
                    msg_p = (
                        f"⭐ *LIQUIDACIÓN PENDIENTE - {nombre_ficha_label}* ⭐\n"
                        f"📅 *Fecha:* {asis_p.fecha} | 🕒 *Turno:* {asis_p.turno}\n"
                        f"-----------------------------------------\n"
                        f"💼 *COMISIONES DE TRAGOS:*\n"
                        f"{tragos_detalle_p}"
                        f"-----------------------------------------\n"
                        f"➕ *BONOS:*\n"
                        f"• Bono de Asistencia: +${monto_bono_p:,.0f}\n"
                    )
                    if dama_p.es_bailarina:
                        msg_p += f"• Ganancia de Bailes: +${ganancia_bailes_p:,.0f}\n"
                    msg_p += (
                        f"-----------------------------------------\n"
                        f"➖ *DESCUENTOS:*\n"
                        f"• Descuento Residencia: -${costo_residencia_p:,.0f}\n"
                        f"-----------------------------------------\n"
                        f"💵 *TOTAL NETO A RECIBIR:*\n"
                        f"👉 *${total_chica_p:,.0f}*\n"
                        f"-----------------------------------------\n"
                        f"_¡Muchas gracias!_ 🌸"
                    )

                # Sanitizar número de WhatsApp
                whatsapp_p_limpio = "".join(c for c in dama_p.whatsapp if c.isdigit()) if dama_p.whatsapp else ""
                msg_p_encoded = urllib.parse.quote(msg_p)
                link_wa_p = f"https://wa.me/{whatsapp_p_limpio}?text={msg_p_encoded}"

                pendientes_global.append({
                    "dama_id": dama_p.id,
                    "nombre": nombre_ficha_label,
                    "fecha": asis_p.fecha,
                    "turno": asis_p.turno,
                    "total_pagar": total_chica_p,
                    "link_wa": link_wa_p
                })

    # --- RETORNO DE LA RESPUESTA
    return templates.TemplateResponse(
        request=request,
        name="reportes.html", 
        context={
            "resumen": resumen, 
            "detalle_damas": detalle_damas,
            "detalle_garzones": detalle_garzones,
            "deudas_global": deudas_global,
            "monto_deudas_total": sum(v.monto for v in deudas_global),
            "logs": logs, 
            "role": user_role,      
            "username": username,   
            "fecha_filtro": fecha_param, 
            "turno_filtro": turno_filter,
            "pendientes_global": pendientes_global # <--- AÑADE ESTA LÍNEA AQUÍ
        }
    )
# ---------------------------------------------------------
# ACCIONES DE VENTA Y PAGOS
# ---------------------------------------------------------
@app.post("/eliminar_venta/{venta_id}")
async def eliminar_venta(request: Request, venta_id: int, motivo: str = Form(...), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "cajera"]:
        return RedirectResponse(url="/", status_code=303)
    
    venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
    if venta:
        # 💡 Corregido: Guardamos el usuario verificado que borró la venta con su motivo en el log de auditoría
        log = models.LogAuditoria(
            usuario=username, 
            accion=f"ELIMINÓ VENTA: {venta.servicio} (Garzón: {venta.mesero}) - MOTIVO: {motivo}",
            turno=venta.turno
        )
        db.add(log)
        db.delete(venta)
        db.commit()
    return RedirectResponse(url="/contabilidad", status_code=303)

@app.post("/registrar_pago_dama/{dama_id}")
async def registrar_pago_dama(request: Request, dama_id: int, fecha: str = Form(...), turno: str = Form(...), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "cajera"]:
        raise HTTPException(status_code=403, detail="No autorizado.")
    
    # 2. PROCESO DE FECHAS (Tu lógica original)
    f_obj = datetime.strptime(fecha, "%Y-%m-%d")
    ini = datetime.combine(f_obj.date(), time.min)
    fn = datetime.combine(f_obj.date(), time.max)
    
    # 3. Liquidar ventas de ese turno
    ventas_pendientes = db.query(models.Venta).filter(
        models.Venta.dama_id == dama_id,
        models.Venta.fecha >= ini,
        models.Venta.fecha <= fn,
        models.Venta.turno == turno,
        models.Venta.liquidada == False
    ).all()
    
    for v in ventas_pendientes: 
        v.liquidada = True

    # 4. Liquidar la asistencia (Bonos/Residencia)
    asis = db.query(models.Asistencia).filter(
        models.Asistencia.dama_id == dama_id,
        models.Asistencia.fecha == fecha,
        models.Asistencia.turno == turno
    ).first()
    
    if asis: 
        asis.liquidada = True

    # 5. AUDITORÍA REAL
    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    # Registramos exactamente quién hizo el pago
    log = models.LogAuditoria(
        usuario=username, 
        accion=f"PAGO PERSONAL: {dama.nombre_artistico} - FECHA: {fecha} - TURNO: {turno}",
        turno=turno  # 💡 <-- AGREGAR ESTA LÍNEA
    )
    db.add(log)
    db.commit()

    return {"status": "ok"}

@app.post("/cobrar_deuda/{venta_id}")
async def cobrar_deuda(
    request: Request,
    venta_id: int, 
    metodo_pago_final: str = Form(...), 
    db: Session = Depends(get_db)
):
    # 🔒 1. VERIFICACIÓN DE SEGURIDAD
    from services.auth_service import obtener_usuario_sesion
    username, user_role = obtener_usuario_sesion(request)

    if user_role not in ["admin1", "cajera"]:
        return RedirectResponse(url="/", status_code=303)
    
    # Obtener configuración del club para conocer el turno activo de hoy
    conf = obtener_config(db)
    
    # 2. PROCESO DE COBRO
    venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
    if venta:
        metodo_anterior = venta.metodo_pago
        fecha_anterior = venta.fecha.strftime("%d/%m/%Y %H:%M") if venta.fecha else "N/A"
        
        venta.metodo_pago = metodo_pago_final
        venta.fecha = obtener_ahora_local()            # Registramos el cobro contablemente HOY
        venta.turno = conf.turno_activo          # Lo metemos al turno activo de hoy
        # -------------------------------------
        
        # 📝 3. AUDITORÍA COMPLETA
        log = models.LogAuditoria(
            usuario=username, 
            accion=(
                f"COBRÓ CUENTA DE: {venta.cliente_nombre or 'CLIENTE'} (${venta.monto:,.0f}) "
                f"| ANTES: {metodo_anterior} ({fecha_anterior}) "
                f"-> COBRADO HOY COMO: {metodo_pago_final} (Turno: {conf.turno_activo})"
            ),
            turno=conf.turno_activo  # 💡 <-- AGREGAR ESTA LÍNEA
        )
        db.add(log)
        db.commit()

    return RedirectResponse(url="/contabilidad", status_code=303)


@app.post("/registrar_pago_show")
async def registrar_pago_show(
    request: Request,
    asistencia_id: int = Form(...), 
    monto_total: float = Form(...), 
    db: Session = Depends(get_db)
):
    # 🔒 1. SEGURIDAD SEGURA CONTRA HACKERS (Verifica la firma digital)
    username, user_role = obtener_usuario_sesion(request)
    
    if not username:
        return {"status": "error", "message": "No autorizado"}

    # 2. TU LÓGICA DE NEGOCIO ORIGINAL (Permanecerá intacta)
    asis = db.query(models.Asistencia).filter(models.Asistencia.id == asistencia_id).first()
    
    if asis:
        asis.bono_show = monto_total
        # 🔄 Si el monto es mayor a 0, está bailando. Si es 0, vuelve a espera.
        asis.bailando_hoy = (monto_total > 0)
        
        dama = db.query(models.Dama).filter(models.Dama.id == asis.dama_id).first()
        accion_txt = f"ACTUALIZÓ SHOWS {dama.nombre_artistico}: ${monto_total:,.0f}" if monto_total > 0 else f"QUITÓ DE PISTA A {dama.nombre_artistico}"
        
        # Guardamos la auditoría con el username verificado y seguro
        db.add(models.LogAuditoria(
            usuario=username, 
            accion=accion_txt,
            turno=asis.turno  # 💡 <-- AGREGAR ESTA LÍNEA
        ))
        db.commit()

    return {"status": "ok"}