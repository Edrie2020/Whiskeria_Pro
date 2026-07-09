# START OF FILE services/ventas_service.py

def calcular_venta_detallada(tier_precio, extra_tipo=None, monto_casa_manual=0, monto_chica_manual=0):
    # 1. Trago Base (40% Dama / 60% Casa)
    total = tier_precio
    pago_chica = tier_precio * 0.4
    pago_casa = tier_precio * 0.6

    # 2. Sumar Extras (VIP va entero a la casa, VIP 2 se divide mitad y mitad)
    if extra_tipo == "VIP":
        total += 50000
        pago_casa += 50000 
    elif extra_tipo == "VIP 2" or extra_tipo == "PRIVADO":
        total += 200000
        pago_chica += 100000 
        pago_casa += 100000  

    # 3. Sumar Salidas Manuales (Se añaden directamente a los acumuladores)
    total += monto_casa_manual + monto_chica_manual
    pago_chica += monto_chica_manual
    pago_casa += monto_casa_manual

    return total, pago_chica, pago_casa
# END OF FILE services/ventas_service.py