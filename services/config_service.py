from sqlalchemy.orm import Session
import models
def obtener_config(db: Session):
    conf = db.query(models.Configuracion).first()
    if not conf:
        conf = models.Configuracion(
            estado_club="CERRADO",
            turno_activo="Turno 1",
            meta_diaria=3000000.0
        )
        db.add(conf)
        db.commit()
        db.refresh(conf)
    return conf