from datetime import date
import models

def registrar_asistencia(dama, turno, tipo_llegada, hora_libro):

    if turno == "Turno 1":
        dama.dias_t1 += 1
    else:
        dama.dias_t2 += 1

    asistencia = models.Asistencia(
        dama_id=dama.id,
        tipo_llegada=tipo_llegada if tipo_llegada else "T2",
        turno=turno,
        hora_libro=hora_libro if hora_libro else "00:00",
        bono_asistencia=0,
        fecha=date.today().strftime("%Y-%m-%d")
    )

    return asistencia