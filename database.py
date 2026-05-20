import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 🔒 Detección automática de la nube (Evita pérdida de datos en producción)
if os.environ.get("RENDER"):
    # Si estamos en Render, guardamos la base de datos en el volumen persistente /data
    os.makedirs("/data", exist_ok=True)
    SQLALCHEMY_DATABASE_URL = "sqlite:////data/whiskeria.db"
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