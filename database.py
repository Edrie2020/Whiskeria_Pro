import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 🔒 Detección automática de la nube (Evita pérdida de datos en producción)
if os.environ.get("RENDER"):
    try:
        # Intentamos usar el disco persistente /data (si existe en plan de pago)
        os.makedirs("/data", exist_ok=True)
        SQLALCHEMY_DATABASE_URL = "sqlite:////data/whiskeria.db"
    except PermissionError:
        # Fallback inteligente para la CAPA GRATUITA (Render Free) sin disco montado:
        # Guardamos localmente en el directorio de la aplicación para poder testear gratis
        SQLALCHEMY_DATABASE_URL = "sqlite:///./whiskeria.db"
else:
    # Si estamos trabajando en nuestra computadora local
    SQLALCHEMY_DATABASE_URL = "sqlite:///./whiskeria.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- FUNCIÓN DE CONEXIÓN CENTRALIZADA ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()