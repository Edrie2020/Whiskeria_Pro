from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from database import Base
from datetime import datetime
from services.time_service import obtener_ahora_local


class Dama(Base):
    __tablename__ = "damas"
    id = Column(Integer, primary_key=True, index=True)
    nombre_artistico = Column(String, unique=True, index=True) 
    nombre_real = Column(String)
    tipo_documento = Column(String, default="RUT")
    rut = Column(String, unique=True, index=True) 
    whatsapp = Column(String, unique=True) 
    foto_url = Column(String)
    es_bailarina = Column(Boolean, default=False)
    esta_activa = Column(Boolean, default=True)
    dias_t1 = Column(Integer, default=0)
    dias_t2 = Column(Integer, default=0)
    ultima_asistencia = Column(DateTime, default=obtener_ahora_local)

class Venta(Base):
    __tablename__ = "ventas"
    id = Column(Integer, primary_key=True, index=True)
    dama_id = Column(Integer, ForeignKey("damas.id"), nullable=True)
    servicio = Column(String) 
    monto = Column(Float)
    comision_chica = Column(Float)
    ganancia_casa = Column(Float)
    turno = Column(String)
    mesero = Column(String) 
    metodo_pago = Column(String, default="EFECTIVO") 
    fecha = Column(DateTime, default=obtener_ahora_local)
    cliente_nombre = Column(String, nullable=True)
    liquidada = Column(Boolean, default=False) 
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=True) 

class Asistencia(Base):
    __tablename__ = "asistencias"
    id = Column(Integer, primary_key=True, index=True)
    dama_id = Column(Integer, ForeignKey("damas.id"))
    
    # Fecha operativa con zona horaria local segura
    fecha = Column(String, default=lambda: obtener_ahora_local().strftime("%Y-%m-%d"))
    
    turno = Column(String)  # <--- ❌ ¡ASEGÚRATE DE QUE ESTA LÍNEA ESTÉ AQUÍ!
    tipo_llegada = Column(String)
    hora_libro = Column(String)
    bono_asistencia = Column(Float, default=0.0)
    bailando_hoy = Column(Boolean, default=False)
    cantidad_shows = Column(Integer, default=0)
    bono_show = Column(Float, default=0.0)
    liquidada = Column(Boolean, default=False)

class Configuracion(Base):
    __tablename__ = "configuracion"
    id = Column(Integer, primary_key=True)
    estado_club = Column(String, default="CERRADO")
    turno_activo = Column(String, default="Turno 1")
    meta_diaria = Column(Float, default=3000000.0)

class LogAuditoria(Base):
    __tablename__ = "logs_auditoria"
    id = Column(Integer, primary_key=True)
    usuario = Column(String)
    accion = Column(String)
    fecha = Column(DateTime, default=obtener_ahora_local)
    turno = Column(String, nullable=True) 
    
class Mesero(Base):
    __tablename__ = "meseros"
    id = Column(Integer, primary_key=True)
    nombre = Column(String, unique=True)

class Producto(Base):
    __tablename__ = "productos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True)
    tipo = Column(String) 
    inicio = Column(Integer, default=0)
    reposicion = Column(Integer, default=0)
    faltante = Column(Integer, default=0)
     # 🍾 CONTROL DE CORTOS Y BOTELLAS
    capacidad_cortos = Column(Integer, nullable=True) # 10, 13, 20 o 26
    es_corto = Column(Boolean, default=False)
    parent_botella_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=True)

class StockMovimiento(Base):
    __tablename__ = "stock_movimientos"
    id = Column(Integer, primary_key=True)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=True)
    nombre_respaldo = Column(String, nullable=True) 
    tipo_movimiento = Column(String) 
    cantidad = Column(Integer)
    usuario = Column(String) 
    fecha = Column(String)   
    turno = Column(String)   
    hora = Column(String)

# --- TABLA DE USUARIOS MEJORADA ---
class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    rol = Column(String)  # "jefe" o "garzon"
    activo = Column(Boolean, default=True) # <--- Útil para bloquear accesos
    ultimo_acceso = Column(DateTime, nullable=True) # <--- Para auditoría del jefe

class InventarioTurno(Base):
    __tablename__ = "inventario_turnos"
    id = Column(Integer, primary_key=True)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=True)
    fecha = Column(String)  # Almacena en formato "YYYY-MM-DD"
    turno = Column(String)  # "Turno 1" o "Turno 2"
    inicio = Column(Integer, default=0) # Stock con el que inicia este turno congelado

class CierreTurno(Base):
    __tablename__ = "cierres_turno"
    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, default=obtener_ahora_local)  # <-- Usando nuestra hora local segura
    turno = Column(String)
    total_ventas = Column(Float)
    total_comisiones_damas = Column(Float)
    total_bonos = Column(Float)
    utilidad_neta_casa = Column(Float)  