from datetime import datetime
import pytz

# 🌎 CONFIGURA AQUÍ TU ZONA HORARIA LOCAL:
# Ejemplos: "America/Santiago" (Chile), "America/Bogota" (Colombia), "America/Argentina/Buenos_Aires", etc.
ZONA_HORARIA_LOCAL = "America/Santiago"

def obtener_ahora_local() -> datetime:
    """
    Retorna la fecha y hora actual convertida a la zona horaria del club,
    limpiando la información de zona para que sea 100% compatible con SQLite.
    """
    zona = pytz.timezone(ZONA_HORARIA_LOCAL)
    ahora_con_zona = datetime.now(zona)
    
    # Limpiamos tzinfo (.replace(tzinfo=None)) para evitar conflictos con SQLite/SQLAlchemy
    return ahora_con_zona.replace(tzinfo=None)