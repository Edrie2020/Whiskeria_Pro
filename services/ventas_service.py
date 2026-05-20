def calcular_venta_detallada(tier_precio, extra_tipo=None, monto_casa_manual=0, monto_chica_manual=0):
    # Caso Salida Manual
    if tier_precio == 0:
        total = monto_casa_manual + monto_chica_manual
        return total, monto_chica_manual, monto_casa_manual

    # 1. Trago Base (40% Dama / 60% Casa)
    total = tier_precio
    pago_chica = tier_precio * 0.4
    pago_casa = tier_precio * 0.6

    # 2. Sumar Extras
    if extra_tipo == "VIP":
        total += 50000
        pago_casa += 50000 # VIP va entero a la casa
    elif extra_tipo == "PRIVADO":
        total += 200000
        pago_chica += 100000 # 100k para la dama
        pago_casa += 100000  # 100k para la casa

    return total, pago_chica, pago_casa