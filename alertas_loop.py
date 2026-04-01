# alertas_loop.py
# Corre en la terminal y revisa cada 30 minutos de 8AM a 6PM
# Déjalo corriendo en una terminal minimizada

import time
from datetime import datetime

from ingesta.alertas_tiempo_real import run as run_alertas

print("Sistema de alertas iniciado. Ctrl+C para detener.")

while True:
    ahora = datetime.now()
    hora  = ahora.hour

    if 8 <= hora < 18:
        print(f"\n[{ahora.strftime('%H:%M')}] Revisando adjudicaciones...")
        run_alertas()
        print(f"Próxima revisión en 30 minutos.")
    else:
        print(f"[{ahora.strftime('%H:%M')}] Fuera de horario. Esperando...")

    time.sleep(1800)  # esperar 30 minutos
