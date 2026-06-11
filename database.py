# START OF FILE database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.event import listens_for

# 🔒 Detección automática de la nube (Evita pérdida de datos en producción)
if os.environ.get("RENDER"):
    try:
        # Intentamos usar el disco persistente /data (si existe en plan de pago)
        os.makedirs("/data", exist_ok=True)
        SQLALCHEMY_DATABASE_URL = "sqlite:////data/whiskeria.db"
    except PermissionError:
        # Fallback inteligente para la CAPA GRATUITA (Render Free) sin disco montado:
        SQLALCHEMY_DATABASE_URL = "sqlite:///./whiskeria.db"
else:
    # Si estamos trabajando en nuestra computadora local
    SQLALCHEMY_DATABASE_URL = "sqlite:///./whiskeria.db"

# Se configura 'timeout': 30 para evitar bloqueos de concurrencia (SQLite Lock)
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False, "timeout": 30}
)

# ⚡ ACTIVACIÓN DE MODO WAL PARA EVITAR CAÍDAS POR CONCURRENCIA EN SQLITE
@listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- FUNCIÓN DE CONEXIÓN CENTRALIZADA ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
# END OF FILE database.py