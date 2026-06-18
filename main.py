# START OF FILE main.py
# =========================================================
# 1. IMPORTACIONES DEL SISTEMA (Librerías externas)
# =========================================================
from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime, time, timedelta
from sqlalchemy import text 
import uvicorn
import urllib.parse
import os 
import shutil

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
    """
    Inicializa el inventario de forma estrictamente aislada por catálogo de turno.
    """
    existe = db.query(models.InventarioTurno).filter(
        models.InventarioTurno.fecha == fecha_hoy,
        models.InventarioTurno.turno == turno_nuevo
    ).first()
    
    if existe:
        return

    # Cargamos únicamente los productos que pertenecen al catálogo de este turno específico y no estén borrados
    productos = db.query(models.Producto).filter(
        models.Producto.turno == turno_nuevo,
        models.Producto.borrado == False
    ).all()
    
    for p in productos:
        ultimo_registro = db.query(models.InventarioTurno).filter(
            models.InventarioTurno.producto_id == p.id,
            models.InventarioTurno.turno == turno_nuevo
        ).order_by(models.InventarioTurno.fecha.desc(), models.InventarioTurno.id.desc()).first()
        
        if not ultimo_registro:
            stock_inicial = p.inicio if p.inicio else 0
        else:
            salida_directa = db.query(func.count(models.Venta.id)).filter(
                models.Venta.producto_id == p.id,
                models.Venta.fecha_operativa == ultimo_registro.fecha,
                models.Venta.turno == ultimo_registro.turno
            ).scalar() or 0
            
            botellas_debitadas_por_cortos = 0
            if p.tipo == "BOTELLA" and p.capacidad_cortos:
                corto_vinculado = db.query(models.Producto).filter(
                    models.Producto.parent_botella_id == p.id,
                    models.Producto.borrado == False
                ).first()
                if corto_vinculado:
                    cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
                        models.Venta.producto_id == corto_vinculado.id,
                        models.Venta.fecha_operativa == ultimo_registro.fecha,
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
                models.StockMovimiento.tipo_movimiento.in_(['FALTANTE', 'APERTURA BOTELLA']),
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

# MIGRACIÓN AUTOMÁTICA SEGURA PARA PRODUCCIÓN (EVITA PÉRDIDA DE DATOS)
def ejecutar_migraciones_sqlite_produccion():
    db = SessionLocal()
    try:
        cursor = db.execute(text("PRAGMA table_info(productos)"))
        columnas_prod = [row[1] for row in cursor.fetchall()]
        
        if "turno" not in columnas_prod:
            db.execute(text("ALTER TABLE productos ADD COLUMN turno VARCHAR DEFAULT 'Turno 1'"))
            db.commit()
            print("✅ MIGRACIÓN: Columna 'turno' inyectada en 'productos'.")

        if "borrado" not in columnas_prod:
            db.execute(text("ALTER TABLE productos ADD COLUMN borrado BOOLEAN DEFAULT 0"))
            db.commit()
            print("✅ MIGRACIÓN: Columna 'borrado' inyectada en 'productos'.")
            
        try:
            db.execute(text("DROP INDEX IF EXISTS ix_productos_nombre"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_productos_nombre ON productos (nombre)"))
            db.commit()
            print("✅ MIGRACIÓN: Índice único de nombres de productos removido.")
        except Exception as idx_err:
            print(f"⚠️ MIGRACIÓN (Aviso de índice): {str(idx_err)}")

        # MIGRACIÓN DE LA TABLA VENTAS PARA COBRO MIXTO
        cursor_v = db.execute(text("PRAGMA table_info(ventas)"))
        columnas_ventas = [row[1] for row in cursor_v.fetchall()]

        if "monto_efectivo" not in columnas_ventas:
            db.execute(text("ALTER TABLE ventas ADD COLUMN monto_efectivo FLOAT DEFAULT 0.0"))
            db.commit()
            print("✅ MIGRACIÓN: Columna 'monto_efectivo' inyectada en 'ventas'.")

        if "monto_tarjeta" not in columnas_ventas:
            db.execute(text("ALTER TABLE ventas ADD COLUMN monto_tarjeta FLOAT DEFAULT 0.0"))
            db.commit()
            print("✅ MIGRACIÓN: Columna 'monto_tarjeta' inyectada en 'ventas'.")

    except Exception as e:
        print(f"⚠️ MIGRACIÓN (Error en migración automática): {str(e)}")
    finally:
        db.close()

# Ejecutamos la migración antes de que el servidor FastAPI empiece a recibir peticiones
ejecutar_migraciones_sqlite_produccion()


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Registro de Routers
app.include_router(ventas.router)
app.include_router(asistencia.router)
app.include_router(damas.router)
app.include_router(garzones_routes.router)
app.include_router(stock.router)
app.include_router(auth.router)
app.include_router(usuarios.router)

# ⚡ MANEJADOR GLOBAL DE EXCEPCIONES PARA EVITAR CAÍDAS DEL SISTEMA (EVITA ERROR 500)
from sqlalchemy.exc import IntegrityError
@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    # Detecta de qué formulario proviene el error y lo redirige con un mensaje descriptivo
    referring_url = request.headers.get("referer", "/")
    base_url = referring_url.split("?")[0]
    
    # Mensaje simplificado y descriptivo
    error_detalle = "Error: El RUT, Nombre Artístico, Nombre de Usuario o Teléfono ingresado ya se encuentra registrado en el sistema."
    
    return RedirectResponse(
        url=f"{base_url}?error={urllib.parse.quote(error_detalle)}", 
        status_code=303
    )

# ---------------------------------------------------------
# RUTA PRINCIPAL (DASHBOARD) - FILTRADO DINÁMICO POR TURNO
# ---------------------------------------------------------
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)

    if not username or user_role not in ["admin1", "administrador", "cajera", "jefe_guillermo", "encargado"]:
        return RedirectResponse(url="/login", status_code=303)
    
    conf = obtener_config(db)
    hoy_dt = obtener_fecha_operativa()
    fecha_hoy_str = hoy_dt.strftime("%Y-%m-%d")
    
    total_ventas = db.query(func.sum(models.Venta.monto)).filter(
        models.Venta.fecha_operativa == fecha_hoy_str,
        models.Venta.turno == conf.turno_activo
    ).scalar() or 0.0
    
    total_deudas = db.query(func.sum(models.Venta.monto)).filter(
        models.Venta.metodo_pago == "CUENTA"
    ).scalar() or 0.0
    
    asistencias = db.query(models.Asistencia).filter(
        models.Asistencia.fecha == fecha_hoy_str,
        models.Asistencia.turno == conf.turno_activo
    ).all()
    
    ids_presentes = [a.dama_id for a in asistencias]
    dict_asistencias = {a.dama_id: a for a in asistencias}
    
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
    
    bailando_hoy = []
    en_espera = []
    ausentes_b = []

    if conf.turno_activo == "Turno 1":
        todas_b = db.query(models.Dama).filter(
            models.Dama.es_bailarina == True, 
            models.Dama.esta_activa == True,
            models.Dama.borrada == False
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

    meta_fija = 4000000.0
    porcentaje = (total_ventas / meta_fija) * 100 if meta_fija > 0 else 0
    if porcentaje > 100: 
        porcentaje = 100

    # Solamente mostrar productos activos en el cooler y venta que no estén borrados
    productos_cooler_filtrados = db.query(models.Producto).filter(
        models.Producto.tipo == "PRODUCTO",
        models.Producto.turno == conf.turno_activo,
        models.Producto.borrado == False
    ).all()

    productos_todos_filtrados = db.query(models.Producto).filter(
        models.Producto.turno == conf.turno_activo,
        models.Producto.borrado == False
    ).all()

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
            "bailando_hoy": bailando_hoy,   
            "en_espera": en_espera,         
            "ausentes_b": ausentes_b,       
            "dict_bailando": {a.dama_id: a.bailando_hoy for a in asistencias},
            "garzones": db.query(models.Mesero).all(),
            "productos_cooler": productos_cooler_filtrados,  
            "productos_todos": productos_todos_filtrados      
        }
    )

# ---------------------------------------------------------
# GESTIÓN DE TURNO (ABRIR/CERRAR) - UNIFICADO Y SEGURO
# ---------------------------------------------------------

@app.post("/gestionar_club")
async def gestionar_club(
    request: Request, 
    accion: str = Form(...), 
    turno: str = Form(None), 
    caja_chica_inicio: float = Form(0.0), 
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if user_role not in ["admin1", "administrador", "cajera"]:
        return RedirectResponse(url="/", status_code=303)

    conf = db.query(models.Configuracion).first()
    if not conf:
        conf = models.Configuracion(estado_club="CERRADO", turno_activo="Turno 1", meta_diaria=3000000.0)
        db.add(conf)
    
    accion_limpia = accion.upper()
    
    if accion_limpia == "ABRIR":
        conf.estado_club = "ABIERTO"
        if turno:
            conf.turno_activo = turno
            
            fecha_op = obtener_fecha_operativa().strftime("%Y-%m-%d")
            inicializar_stock_nuevo_turno(db, fecha_op, turno)
            
            caja_existente = db.query(models.CajaTurno).filter(
                models.CajaTurno.fecha == fecha_op,
                models.CajaTurno.turno == turno
            ).first()
            
            if caja_existente:
                caja_existente.monto_apertura = caja_chica_inicio
            else:
                nueva_caja = models.CajaTurno(
                    fecha=fecha_op,
                    turno=turno,
                    monto_apertura=caja_chica_inicio
                )
                db.add(nueva_caja)
            
    else:
        conf.estado_club = "CERRADO"

    log = models.LogAuditoria(
        usuario=username, 
        accion=f"{accion_limpia} CLUB - {conf.turno_activo}",
        turno=conf.turno_activo  
    )
    db.add(log)
    
    db.commit()
    db.refresh(conf) 
    
    return RedirectResponse(url="/", status_code=303)

# =========================================================================
# CONTABILIDAD Y REPORTES (FUNCIÓN COMPLETA E INTEGRADA)
# =========================================================================
@app.get("/contabilidad", response_class=HTMLResponse)  
async def contabilidad_page(request: Request, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera", "jefe_guillermo", "encargado"]:
        return RedirectResponse(url="/login?error=no_autorizado", status_code=303)
        
    hoy_dt = obtener_fecha_operativa()
    fecha_param = request.query_params.get("fecha", hoy_dt.strftime("%Y-%m-%d"))
    turno_filter = request.query_params.get("turno", "Turno 1")
    
    fecha_obj = datetime.strptime(fecha_param, "%Y-%m-%d")
    inicio_dia = datetime.combine(fecha_obj.date(), time.min)
    fin_dia = datetime.combine(fecha_obj.date(), time.max)

    # 1. Obtener Ventas del turno
    ventas_hoy = db.query(models.Venta).filter(
        models.Venta.fecha_operativa == fecha_param, 
        models.Venta.turno == turno_filter
    ).all()
    
    # 2. Obtener Asistencias del turno
    asistencias_hoy = db.query(models.Asistencia).filter(
        models.Asistencia.fecha == fecha_param, 
        models.Asistencia.turno == turno_filter
    ).all()

    caja_info = db.query(models.CajaTurno).filter(
        models.CajaTurno.fecha == fecha_param,
        models.CajaTurno.turno == turno_filter
    ).first()
    monto_ap_valor = caja_info.monto_apertura if caja_info else 0.0

    # 3. Resumen General
    resumen = {
        "bruto": sum(v.monto for v in ventas_hoy),
        "efectivo": sum(v.monto for v in ventas_hoy if v.metodo_pago == "EFECTIVO") + sum((v.monto_efectivo or 0) for v in ventas_hoy if v.metodo_pago == "MIXTO"),
        "tarjeta": sum(v.monto for v in ventas_hoy if v.metodo_pago == "TARJETA") + sum((v.monto_tarjeta or 0) for v in ventas_hoy if v.metodo_pago == "MIXTO"),
        "transferencia": sum(v.monto for v in ventas_hoy if v.metodo_pago == "TRANSFERENCIA"),
        "monto_apertura": monto_ap_valor,
        "efectivo_total_gaveta": sum(v.monto for v in ventas_hoy if v.metodo_pago == "EFECTIVO") + sum((v.monto_efectivo or 0) for v in ventas_hoy if v.metodo_pago == "MIXTO") + monto_ap_valor
    }

    damas_nombres_dict = {d.id: d.nombre_artistico for d in db.query(models.Dama).all()}

    # 4. Cálculo detallado por Dama (Fichas del Turno - EXCLUYENDO PRIVADOS)
    detalle_damas = []
    for asis in asistencias_hoy:
        dama = db.query(models.Dama).filter(models.Dama.id == asis.dama_id).first()
        if not dama: continue
        
        # Filtramos ventas excluyendo "PRIVADO" para cumplir la instrucción de "se paga aparte"
        ventas_pagadas = [v for v in ventas_hoy if v.dama_id == dama.id and v.liquidada and v.servicio != "PRIVADO"]
        ventas_pendientes = [v for v in ventas_hoy if v.dama_id == dama.id and not v.liquidada and v.servicio != "PRIVADO"]

        # Bonos de Asistencia y Residencia
        monto_bono_base = asis.bono_asistencia or 0.0
        costo_residencia_base = 0
        if asis.tipo_llegada == "Residente":
            costo_residencia_base = 5000

        whatsapp_limpio = "".join(c for c in dama.whatsapp if c.isdigit()) if dama.whatsapp else ""

        # Contar cuántos Privados de hoy tiene la dama para adjuntarle la nota informativa
        privados_hoy_dama = [v for v in ventas_hoy if v.dama_id == dama.id and v.servicio == "PRIVADO"]
        total_privados_hoy_dama_comis = sum(v.comision_chica for v in privados_hoy_dama)
        cant_privados_hoy_dama = len(privados_hoy_dama)

        note_privados_txt = ""
        if cant_privados_hoy_dama > 0:
            note_privados_txt = (
                f"\n-----------------------------------------\n"
                f"💎 *PRIVADOS DEL TURNO (SE PAGAN APARTE):*\n"
                f"• Cantidad: {cant_privados_hoy_dama} Privados\n"
                f"• Comisión por cobrar: *${total_privados_hoy_dama_comis:,.0f}*\n"
                f"_(Se liquidan por separado en el control de privados)_"
            )

        # CASO A: Si ya se pagó la Ficha 1
        if asis.liquidada:
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
                f"_Ficha cerrada con éxito._ ✔"
            )
            msg_f1 += note_privados_txt

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

            # Si hay nuevos tragos no pagados, abrimos de forma automática la FICHA 2 (PENDIENTE)
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
                    f"_¡Muchas gracias!_ 🌸"
                )
                msg_f2 += note_privados_txt

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
                f"_¡Muchas gracias!_ 🌸"
            )
            msg_f1_pend += note_privados_txt

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

    # =========================================================================
    # 5. Cálculo detallado por Garzón (Preservando nombres de damas borradas)
    # =========================================================================
    damas_lookup = {}
    for d in db.query(models.Dama).all():
        nombre_lookup = d.nombre_artistico
        if d.borrada:
            nombre_lookup += " (ELIMINADA)"
        damas_lookup[d.id] = nombre_lookup

    detalle_garzones = {}
    for v in ventas_hoy:
        if v.mesero not in detalle_garzones:
            detalle_garzones[v.mesero] = {"total": 0, "lista": []}
            
        if v.dama_id:
            destino = damas_lookup.get(v.dama_id, "S/D")
        else:
            destino = f"CLIENTE: {v.cliente_nombre}" if v.cliente_nombre else "CLIENTE SOLO"

        detalle_garzones[v.mesero]["total"] += v.monto
        detalle_garzones[v.mesero]["lista"].append({
            "id": v.id,
            "hora": v.fecha.strftime("%H:%M"),
            "servicio": v.servicio,
            "destino": destino,
            "monto": v.monto
        })

    # =========================================================================
    # 5.2. Cálculo del Balance 50/50 y reportes financieros
    # =========================================================================
    # Suma de payouts de las fichas de turno
    total_fichas_damas = sum(d["total_pagar"] for d in detalle_damas)
    
    # Suma de todas las comisiones de Privados LIQUIDADOS hoy (100k por cada uno)
    total_privados_liquidados_hoy = sum(
        v.comision_chica for v in ventas_hoy 
        if v.servicio == "PRIVADO" and v.liquidada
    )
    
    resumen["pagos_damas"] = total_fichas_damas + total_privados_liquidados_hoy
    resumen["neto"] = resumen["bruto"] - resumen["pagos_damas"]

    logs = db.query(models.LogAuditoria).filter(
        models.LogAuditoria.fecha >= inicio_dia, 
        models.LogAuditoria.fecha <= fin_dia,
        models.LogAuditoria.turno == turno_filter
    ).order_by(models.LogAuditoria.fecha.desc()).all()
    
    deudas_global = db.query(models.Venta).filter(models.Venta.metodo_pago == "CUENTA").all()

    # =========================================================================
    # 🔒 7. BÚSQUEDA GLOBAL DE LIQUIDACIONES PENDIENTES (SÓLO DÍAS ANTERIORES)
    # =========================================================================
    asis_no_liq = db.query(models.Asistencia).filter(
        models.Asistencia.liquidada == False
    ).all()

    ventas_pendientes_raw = db.query(models.Venta).filter(
        models.Venta.liquidada == False,
        models.Venta.servicio != "PRIVADO"
    ).all()

    ventas_agrupadas = {}
    claves_ventas_pendientes = set()
    for v in ventas_pendientes_raw:
        if not v.fecha_operativa:
            continue
        clave = (v.dama_id, v.fecha_operativa, v.turno)
        if clave not in ventas_agrupadas:
            ventas_agrupadas[clave] = []
        ventas_agrupadas[clave].append(v)
        claves_ventas_pendientes.add((v.dama_id, v.fecha_operativa, v.turno))

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

    asistencias_pendientes = asis_no_liq + asis_fichas_extras
    asistencias_pendientes.sort(key=lambda x: x.fecha, reverse=True)

    pendientes_global_completo = []

    if asistencias_pendientes:
        damas_dict = {d.id: d for d in db.query(models.Dama).all()}

        for asis_p in asistencias_pendientes:
            dama_p = damas_dict.get(asis_p.dama_id)
            if not dama_p: 
                continue
            
            clave_asis = (asis_p.dama_id, asis_p.fecha, asis_p.turno)
            ventas_p = [v for v in ventas_agrupadas.get(clave_asis, [])]
            
            total_comis_p = sum(v.comision_chica for v in ventas_p)

            if asis_p.liquidada:
                monto_bono_p = 0
                costo_residencia_p = 0
                ganancia_bailes_p = 0
                nombre_ficha_label = f"{dama_p.nombre_artistico} (FICHA 2)"
            else:
                monto_bono_p = asis_p.bono_asistencia or 0.0
                costo_residencia_p = 0
                if asis_p.tipo_llegada == "Residente":
                    costo_residencia_p = 5000
                ganancia_bailes_p = asis_p.bono_show
                nombre_ficha_label = f"{dama_p.nombre_artistico} (FICHA 1)"
                
            total_chica_p = (total_comis_p + monto_bono_p + ganancia_bailes_p) - costo_residencia_p

            if total_chica_p > 0:
                tragos_detalle_p = ""
                for v_p in ventas_p:
                    tragos_detalle_p += f"• {v_p.fecha.strftime('%H:%M')} - {v_p.servicio} (+${v_p.comision_chica:,.0f}) [Garzón: {v_p.mesero}]\n"
                
                if not tragos_detalle_p:
                    tragos_detalle_p = "• Sin consumos.\n"

                # Adjuntar también a las fichas pendientes del historial la nota informativa de privados de ese día
                priv_hist_dama = db.query(models.Venta).filter(
                    models.Venta.dama_id == dama_p.id,
                    models.Venta.servicio == "PRIVADO",
                    models.Venta.fecha_operativa == asis_p.fecha,
                    models.Venta.turno == asis_p.turno
                ).all()
                total_priv_hist_comis = sum(v.comision_chica for v in priv_hist_dama)
                cant_priv_hist = len(priv_hist_dama)
                
                note_hist_txt = ""
                if cant_priv_hist > 0:
                    note_hist_txt = (
                        f"\n-----------------------------------------\n"
                        f"💎 *PRIVADOS DEL TURNO (SE PAGAN APARTE):*\n"
                        f"• Cantidad: {cant_priv_hist} Privados\n"
                        f"• Comisión por cobrar: *${total_priv_hist_comis:,.0f}*\n"
                        f"_(Se liquidan por separado en el control de privados)_"
                    )

                if asis_p.liquidada:
                    msg_p = (
                        f"⭐ *DETALLE DE LIQUIDACIÓN - {nombre_ficha_label}* ⭐\n"
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
                        f"⭐ *DETALLE DE LIQUIDACIÓN - {nombre_ficha_label}* ⭐\n"
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
                
                msg_p += note_hist_txt

                whatsapp_p_limpio = "".join(c for c in dama_p.whatsapp if c.isdigit()) if dama_p.whatsapp else ""
                msg_p_encoded = urllib.parse.quote(msg_p)
                link_wa_p = f"https://wa.me/{whatsapp_p_limpio}?text={msg_p_encoded}"

                pendientes_global_completo.append({
                    "dama_id": dama_p.id,
                    "nombre": f"{dama_p.nombre_artistico} (ELIMINADA) (FICHA 2)" if dama_p.borrada and asis_p.liquidada else (f"{dama_p.nombre_artistico} (ELIMINADA) (FICHA 1)" if dama_p.borrada else nombre_ficha_label),
                    "fecha": asis_p.fecha,
                    "turno": asis_p.turno,
                    "total_pagar": total_chica_p,
                    "link_wa": link_wa_p
                })

    # Filtrar para que solo aparezcan fichas de días anteriores en el modal de pendientes
    pendientes_global = [p for p in pendientes_global_completo if p["fecha"] != fecha_param or p["turno"] != turno_filter]

    # =========================================================================
    # 🔒 AGRUPACIÓN DE PRIVADOS POR DAMA (FECHAS Y TURNOS DETALLADOS)
    # =========================================================================
    privados_todos = db.query(models.Venta).filter(models.Venta.servicio == "PRIVADO").all()
    
    grupos_hoy = {}
    grupos_pendientes = {}
    
    for p in privados_todos:
        clave = (p.dama_id, p.fecha_operativa, p.turno)
        
        if p.fecha_operativa == fecha_param and p.turno == turno_filter:
            if clave not in grupos_hoy:
                grupos_hoy[clave] = []
            grupos_hoy[clave].append(p)
        else:
            if not p.liquidada:
                if clave not in grupos_pendientes:
                    grupos_pendientes[clave] = []
                grupos_pendientes[clave].append(p)
                
    lista_privados_cuentas_hoy = []
    for (dama_id, f_op, tur), ventas_grupo in grupos_hoy.items():
        dama_nombre = damas_nombres_dict.get(dama_id, "S/D")
        cant_priv = len(ventas_grupo)
        total_comis = sum(v.comision_chica for v in ventas_grupo)
        group_liq = all(v.liquidada for v in ventas_grupo)
        ids_csv = ",".join(str(v.id) for v in ventas_grupo)
        
        # Desglose detallado de cada consumo individual para el modal
        consumos_detallados = []
        msg_detalle_wa = ""
        for v in ventas_grupo:
            hora_f = v.fecha.strftime('%H:%M')
            cli = v.cliente_nombre or "CLIENTE"
            consumos_detallados.append({
                "id": v.id,
                "hora": hora_f,
                "garzon": v.mesero,
                "cliente": cli,
                "monto": v.monto,
                "comision": v.comision_chica,
                "liquidada": v.liquidada
            })
            msg_detalle_wa += f"• {hora_f} - PRIVADO (+${v.comision_chica:,.0f}) [Garzón: {v.mesero}]\n"

        # Mensaje de WhatsApp personalizado para los Privados del turno activo
        dama_obj = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
        wsp_limpio = "".join(c for c in dama_obj.whatsapp if c.isdigit()) if dama_obj and dama_obj.whatsapp else ""
        
        msg_wa = (
            f"⭐ *DETALLE DE PRIVADOS - {dama_nombre}* ⭐\n"
            f"📅 *Fecha:* {f_op} | 🕒 *Turno:* {tur}\n"
            f"-----------------------------------------\n"
            f"💎 *SERVICIOS DE PRIVADOS:* \n{msg_detalle_wa}"
            f"-----------------------------------------\n"
            f"💵 *TOTAL POR COBRAR:* *${total_comis:,.0f}*\n"
            f"-----------------------------------------\n"
            f"_Se liquidan por separado._ 🌸"
        )
        link_wa = f"https://wa.me/{wsp_limpio}?text={urllib.parse.quote(msg_wa)}"
        
        lista_privados_cuentas_hoy.append({
            "dama_id": dama_id,
            "nombre": dama_nombre,
            "fecha": f_op,
            "turno": tur,
            "cantidad": cant_priv,
            "total_commission": total_comis,
            "liquidada": group_liq,
            "ids_ventas": ids_csv,
            "consumos": consumos_detallados,
            "link_wa": link_wa
        })
        
    lista_privados_pendientes = []
    for (dama_id, f_op, tur), ventas_grupo in grupos_pendientes.items():
        dama_nombre = damas_nombres_dict.get(dama_id, "S/D")
        cant_priv = len(ventas_grupo)
        total_comis = sum(v.comision_chica for v in ventas_grupo)
        group_liq = all(v.liquidada for v in ventas_grupo)
        ids_csv = ",".join(str(v.id) for v in ventas_grupo)
        
        consumos_detallados = []
        msg_detalle_wa = ""
        for v in ventas_grupo:
            hora_f = v.fecha.strftime('%H:%M')
            cli = v.cliente_nombre or "CLIENTE"
            consumos_detallados.append({
                "id": v.id,
                "hora": hora_f,
                "garzon": v.mesero,
                "cliente": cli,
                "monto": v.monto,
                "comision": v.comision_chica,
                "liquidada": v.liquidada
            })
            msg_detalle_wa += f"• {hora_f} - PRIVADO (+${v.comision_chica:,.0f}) [Garzón: {v.mesero}]\n"

        dama_obj = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
        wsp_limpio = "".join(c for c in dama_obj.whatsapp if c.isdigit()) if dama_obj and dama_obj.whatsapp else ""
        
        msg_wa = (
            f"⭐ *DETALLE DE PRIVADOS HISTÓRICOS - {dama_nombre}* ⭐\n"
            f"📅 *Fecha:* {f_op} | 🕒 *Turno:* {tur}\n"
            f"-----------------------------------------\n"
            f"💎 *SERVICIOS DE PRIVADOS:* \n{msg_detalle_wa}"
            f"-----------------------------------------\n"
            f"💵 *TOTAL POR COBRAR:* *${total_comis:,.0f}*\n"
            f"-----------------------------------------\n"
            f"_Pendiente de días anteriores._ 🌸"
        )
        link_wa = f"https://wa.me/{wsp_limpio}?text={urllib.parse.quote(msg_wa)}"
        
        lista_privados_pendientes.append({
            "dama_id": dama_id,
            "nombre": dama_nombre,
            "fecha": f_op,
            "turno": tur,
            "cantidad": cant_priv,
            "total_commission": total_comis,
            "liquidada": group_liq,
            "ids_ventas": ids_csv,
            "consumos": consumos_detallados,
            "link_wa": link_wa
        })
        
    total_unpaid_privados_hoy = sum(v.comision_chica for v in privados_todos if v.fecha_operativa == fecha_param and v.turno == turno_filter and not v.liquidada)
    total_unpaid_privados_historial = sum(v.comision_chica for v in privados_todos if (v.fecha_operativa != fecha_param or v.turno != turno_filter) and not v.liquidada)

    # Privados del día actual agrupados para el listado del modal
    lista_privados_dia = []
    for (dama_id, f_op, tur), ventas_grupo in grupos_hoy.items():
        dama_nombre = damas_nombres_dict.get(dama_id, "S/D")
        cant_priv = len(ventas_grupo)
        total_comis = sum(v.comision_chica for v in ventas_grupo)
        group_liq = all(v.liquidada for v in ventas_grupo)
        ids_csv = ",".join(str(v.id) for v in ventas_grupo)
        
        consumos_detallados = []
        for v in ventas_grupo:
            consumos_detallados.append({
                "id": v.id,
                "hora": v.fecha.strftime('%H:%M'),
                "garzon": v.mesero,
                "cliente": v.cliente_nombre or "CLIENTE",
                "monto": v.monto,
                "comision": v.comision_chica,
                "liquidada": v.liquidada
            })
        
        lista_privados_dia.append({
            "dama_id": dama_id,
            "nombre": dama_nombre,
            "fecha": f_op,
            "turno": tur,
            "cantidad": cant_priv,
            "total_commission": total_comis,
            "liquidada": group_liq,
            "ids_ventas": ids_csv,
            "consumos": consumos_detallados
        })

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
            "pendientes_global": pendientes_global,
            "privados_cuentas_hoy": lista_privados_cuentas_hoy,
            "total_privados_hoy": total_unpaid_privados_hoy,
            "privados_pendientes": lista_privados_pendientes,
            "total_privados_pendientes": total_unpaid_privados_historial,
            "privados_dia": lista_privados_dia
        }
    )

# ---------------------------------------------------------
# ACCIONES DE VENTA Y PAGOS
# ---------------------------------------------------------
@app.post("/eliminar_venta/{venta_id}")
async def eliminar_venta(request: Request, venta_id: int, motivo: str = Form(...), db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        raise HTTPException(status_code=403, detail="No authorized.")

    venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
    if venta:
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
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        raise HTTPException(status_code=403, detail="No autorizado.")
    
    # Liquidar ventas de ese turno usando la columna fecha_operativa exacta (excluye privados de la liquidación de ficha)
    ventas_pendientes = db.query(models.Venta).filter(
        models.Venta.dama_id == dama_id,
        models.Venta.fecha_operativa == fecha,
        models.Venta.turno == turno,
        models.Venta.servicio != "PRIVADO",
        models.Venta.liquidada == False
    ).all()
    
    for v in ventas_pendientes: 
        v.liquidada = True

    # Liquidar la asistencia (Bonos/Residencia)
    asis = db.query(models.Asistencia).filter(
        models.Asistencia.dama_id == dama_id,
        models.Asistencia.fecha == fecha,
        models.Asistencia.turno == turno
    ).first()
    
    if asis: 
        asis.liquidada = True

    dama = db.query(models.Dama).filter(models.Dama.id == dama_id).first()
    log = models.LogAuditoria(
        usuario=username, 
        accion=f"PAGO PERSONAL: {dama.nombre_artistico} - FECHA: {fecha} - TURNO: {turno}",
        turno=turno  
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
    username, user_role = obtener_usuario_sesion(request)

    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        raise HTTPException(status_code=403, detail="No autorizado.")
    
    conf = obtener_config(db)
    
    venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
    if venta:
        metodo_anterior = venta.metodo_pago
        fecha_anterior = venta.fecha.strftime("%d/%m/%Y %H:%M") if venta.fecha else "N/A"
        
        venta.metodo_pago = metodo_pago_final
        venta.fecha = obtener_ahora_local()            
        venta.turno = conf.turno_activo          
        
        log = models.LogAuditoria(
            usuario=username, 
            accion=(
                f"COBRÓ CUENTA DE: {venta.cliente_nombre or 'CLIENTE'} (${venta.monto:,.0f}) "
                f"| ANTES: {metodo_anterior} ({fecha_anterior}) "
                f"-> COBRADO HOY COMO: {metodo_pago_final} (Turno: {conf.turno_activo})"
            ),
            turno=conf.turno_activo  
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
    username, user_role = obtener_usuario_sesion(request)
    
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        return {"status": "error", "message": "No autorizado"}

    asis = db.query(models.Asistencia).filter(models.Asistencia.id == asistencia_id).first()
    
    if asis:
        asis.bono_show = monto_total
        asis.bailando_hoy = (monto_total > 0)
        
        dama = db.query(models.Dama).filter(models.Dama.id == asis.dama_id).first()
        accion_txt = f"ACTUALIZÓ SHOWS {dama.nombre_artistico}: ${monto_total:,.0f}" if monto_total > 0 else f"QUITÓ DE PISTA A {dama.nombre_artistico}"
        
        db.add(models.LogAuditoria(
            usuario=username, 
            accion=accion_txt,
            turno=asis.turno  
        ))
        db.commit()

    return {"status": "ok"}

@app.get("/descargar_respaldo_secreto")
async def descargar_respaldo_secreto(clave: str):
    if clave != "whiskeria9981":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    ruta_disco_persistente = "/data/whiskeria.db"
    ruta_servidor_gratuito = "./whiskeria.db"
    
    if os.path.exists(ruta_disco_persistente):
        return FileResponse(ruta_disco_persistente, filename="whiskeria_backup.db")
    elif os.path.exists(ruta_servidor_gratuito):
        return FileResponse(ruta_servidor_gratuito, filename="whiskeria_backup.db")
    else:
        raise HTTPException(status_code=404, detail="Base de datos no encontrada")

@app.post("/registrar_pago_privado/{venta_id}")
async def registrar_pago_privado(request: Request, venta_id: int, db: Session = Depends(get_db)):
    username, user_role = obtener_usuario_sesion(request)
    if not username or user_role not in ["admin1", "administrador", "cajera"]:
        raise HTTPException(status_code=403, detail="No autorizado.")
    
    venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
    if venta:
        venta.liquidada = True
        log = models.LogAuditoria(
            usuario=username,
            accion=f"PAGÓ PRIVADO INDIVIDUAL - Venta ID: {venta.id} (${venta.comision_chica:,.0f})",
            turno=venta.turno
        )
        db.add(log)
        db.commit()
    return {"status": "ok"}   

@app.post("/registrar_pago_privado_grupo")
async def registrar_pago_privado_grupo(
    request: Request,
    ids: str = Form(...),
    db: Session = Depends(get_db)
):
    username, user_role = obtener_usuario_sesion(request)
    if user_role not in ["admin1", "administrador", "cajera"]:
        raise HTTPException(status_code=403, detail="No autorizado.")
        
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if id_list:
        ventas_grupo = db.query(models.Venta).filter(models.Venta.id.in_(id_list)).all()
        for v in ventas_grupo:
            v.liquidada = True
        
        db.add(models.LogAuditoria(
            usuario=username,
            accion=f"PAGÓ GRUPO DE PRIVADOS - Cantidad: {len(ventas_grupo)} servicios",
            turno=ventas_grupo[0].turno if ventas_grupo else None
        ))
        db.commit()
    return {"status": "ok"}
    
@app.post("/subir_respaldo_secreto")
async def subir_respaldo_secreto(clave: str, archivo: UploadFile = File(...)):
    if clave != "whiskeria9981":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    ruta_disco_persistente = "/data"
    
    if os.path.exists(ruta_disco_persistente):
        ruta_destino = "/data/whiskeria.db"
    else:
        ruta_destino = "./whiskeria.db"

    try:
        with open(ruta_destino, "wb") as buffer:
            shutil.copyfileobj(archivo.file, buffer)
        return {"status": "success", "message": "Base de datos subida y actualizada con éxito."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al subir el archivo: {str(e)}")
# END OF FILE main.py