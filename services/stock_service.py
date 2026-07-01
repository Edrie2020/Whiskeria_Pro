# START OF FILE services/stock_service.py
from sqlalchemy.orm import Session
from sqlalchemy import func
import models

def propagar_recalculo_stock(db: Session, producto_id: int, fecha_modificada: str, turno: str):
    """
    Recalcula en cascada el stock inicial para todos los turnos posteriores 
    cuando ocurre una modificación histórica (como eliminar una venta o registrar una merma).
    """
    if not producto_id:
        return
        
    p = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not p or p.borrado:
        return

    # Obtenemos todos los registros de inventario de este producto y turno ordenados cronológicamente
    registros = db.query(models.InventarioTurno).filter(
        models.InventarioTurno.producto_id == producto_id,
        models.InventarioTurno.turno == turno
    ).order_by(models.InventarioTurno.fecha.asc()).all()
    
    idx_modificado = -1
    for i, reg in enumerate(registros):
        if reg.fecha == fecha_modificada:
            idx_modificado = i
            break
            
    if idx_modificado == -1:
        return
        
    # Recalculamos el inicio de cada día posterior basándonos en el saldo real del anterior
    for i in range(idx_modificado, len(registros) - 1):
        actual = registros[i]
        siguiente = registros[i + 1]
        
        if p.es_corto:
            parent_botella = db.query(models.Producto).filter(
                models.Producto.id == p.parent_botella_id
            ).first()
            if parent_botella:
                cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
                    models.Venta.producto_id == p.id,
                    models.Venta.fecha_operativa == actual.fecha,
                    models.Venta.turno == actual.turno
                ).scalar() or 0
                nuevo_inicio_siguiente = (actual.inicio + cortos_consumidos) % parent_botella.capacidad_cortos
            else:
                nuevo_inicio_siguiente = 0
        else:
            salida_directa = db.query(func.count(models.Venta.id)).filter(
                models.Venta.producto_id == p.id,
                models.Venta.fecha_operativa == actual.fecha,
                models.Venta.turno == actual.turno
            ).scalar() or 0
            
            botellas_debitadas_por_cortos = 0
            if p.tipo == "BOTELLA" and p.capacidad_cortos:
                corto_vinculado = db.query(models.Producto).filter(
                    models.Producto.parent_botella_id == p.id,
                    models.Producto.borrado == False
                ).first()
                if corto_vinculado:
                    inv_corto_turno = db.query(models.InventarioTurno).filter(
                        models.InventarioTurno.producto_id == corto_vinculado.id,
                        models.InventarioTurno.fecha == actual.fecha,
                        models.InventarioTurno.turno == actual.turno
                    ).first()
                    corto_inicio = inv_corto_turno.inicio if inv_corto_turno else 0
                    
                    cortos_consumidos = db.query(func.count(models.Venta.id)).filter(
                        models.Venta.producto_id == corto_vinculado.id,
                        models.Venta.fecha_operativa == actual.fecha,
                        models.Venta.turno == actual.turno
                    ).scalar() or 0
                    botellas_debitadas_por_cortos = (corto_inicio + cortos_consumidos) // p.capacidad_cortos
                    
            salida_total = salida_directa + botellas_debitadas_por_cortos
            
            repos = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
                models.StockMovimiento.producto_id == p.id,
                models.StockMovimiento.tipo_movimiento == 'REPOSICION',
                models.StockMovimiento.fecha == actual.fecha,
                models.StockMovimiento.turno == actual.turno
            ).scalar() or 0

            falts = db.query(func.sum(models.StockMovimiento.cantidad)).filter(
                models.StockMovimiento.producto_id == p.id,
                models.StockMovimiento.tipo_movimiento.in_(['FALTANTE', 'APERTURA BOTELLA']),
                models.StockMovimiento.fecha == actual.fecha,
                models.StockMovimiento.turno == actual.turno
            ).scalar() or 0
            
            saldo_final = (actual.inicio + repos) - salida_total - falts
            nuevo_inicio_siguiente = max(0, saldo_final)
            
        siguiente.inicio = nuevo_inicio_siguiente
        db.add(siguiente)
        
    db.commit()
# END OF FILE services/stock_service.py