# START OF FILE limpiar_stock.py
from database import SessionLocal
from sqlalchemy import text

def limpiar_inventario_completo():
    db = SessionLocal()
    try:
        print("⏳ Iniciando desvinculación y limpieza de inventario...")
        
        # 1. Establecer en NULL la referencia de producto en ventas para no violar restricciones de Foreign Key
        db.execute(text("UPDATE ventas SET producto_id = NULL"))
        db.commit()
        print("✅ Ventas históricas desvinculadas de productos antiguos de forma segura (la contabilidad monetaria se conserva).")

        # 2. Vaciar las tablas relacionadas con stock en el orden correcto
        db.execute(text("DELETE FROM stock_movimientos"))
        db.execute(text("DELETE FROM inventario_turnos"))
        db.execute(text("DELETE FROM productos"))
        db.commit()
        print("✅ Tablas de productos, movimientos de stock e inventarios por turno vaciadas por completo.")
        
        # 3. Reiniciar las secuencias de IDs en SQLite para que el primer producto comience desde el ID 1
        db.execute(text("DELETE FROM sqlite_sequence WHERE name IN ('productos', 'stock_movimientos', 'inventario_turnos')"))
        db.commit()
        print("✅ Índices autoincrementales reiniciados a 1.")
        
        print("\n🎉 PROCESO FINALIZADO CON ÉXITO. El catálogo de stock está limpio y listo para usarse desde cero.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error durante el proceso de limpieza: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    limpiar_inventario_completo()
# END OF FILE limpiar_stock.py