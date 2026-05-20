from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database import SessionLocal
from database import get_db
import models

router = APIRouter()  # ← importante

@router.post("/agregar_garzon")
def agregar_garzon(nombre: str = Form(...), db: Session = Depends(get_db)):
    nombre = nombre.upper()
    nuevo = models.Mesero(nombre=nombre)
    try:
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        # Devolvemos JSON para que JavaScript lo use
        return {"id": nuevo.id, "nombre": nuevo.nombre, "status": "success"}
    except Exception:
        db.rollback()
        return {"status": "error", "message": "El garzón ya existe"}

@router.get("/eliminar_garzon/{id}")
def eliminar_garzon(id: int, db: Session = Depends(get_db)):
    garzon = db.query(models.Mesero).filter(models.Mesero.id == id).first()
    if garzon:
        db.delete(garzon)
        db.commit()
    return {"status": "deleted"}