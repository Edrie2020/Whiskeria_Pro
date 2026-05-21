from services.time_service import obtener_ahora_local
from datetime import time, timedelta
import models

def registrar_asistencia(dama, turno, tipo_llegada, hora_libro):

    if turno == "Turno 1":
        dama.dias_t1 += 1
    else:
        dama.dias_t2 += 1

    # Calculamos la fecha operativa de Chile de manera segura
    ahora = obtener_ahora_local()
    if ahora.time() < time(6, 0):
        fecha_op = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        fecha_op = ahora.strftime("%Y-%m-%d")

    asistencia = models.Asistencia(
        dama_id=dama.id,
        tipo_llegada=tipo_llegada if tipo_llegada else "T2",
        turno=turno,
        hora_libro=hora_libro if hora_libro else "00:00",
        bono_asistencia=0,
        fecha=fecha_op
    )

    return asistencia