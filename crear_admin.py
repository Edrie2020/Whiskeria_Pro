from database import SessionLocal, engine  # <-- Importamos 'engine'
import models
from services.auth_service import obtener_hash

def crear_primer_usuario():
    # 🛠️ CREAR LAS TABLAS PRIMERO (Esto soluciona el error de "no such table")
    models.Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Datos del primer usuario
    usuario_nombre = "admin"
    clave_plana = "1234" # <--- CAMBIA ESTO POR LA CLAVE QUE QUIERAS
    rol_usuario = "jefe"

    # Revisamos si ya existe para no duplicarlo
    existe = db.query(models.Usuario).filter(models.Usuario.username == usuario_nombre).first()
    
    if not existe:
        nuevo_usuario = models.Usuario(
            username=usuario_nombre,
            password_hash=obtener_hash(clave_plana), # Aquí se encripta
            rol=rol_usuario
        )
        db.add(nuevo_usuario)
        db.commit()
        print(f"✅ USUARIO CREADO EXITOSAMENTE")
        print(f"Usuario: {usuario_nombre}")
        print(f"Clave: {clave_plana}")
        print(f"Rol: {rol_usuario}")
    else:
        print("⚠️ El usuario ya existe en la base de datos.")
    
    db.close()

if __name__ == "__main__":
    crear_primer_usuario()