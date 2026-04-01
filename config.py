# =============================================================
#  config.py — configuración central del proyecto
#  Versión 3 — lee credenciales desde .env
# =============================================================

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
# RUTAS BASE
# ─────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR  / "data"
RAW_DIR    = DATA_DIR  / "raw"
OUTPUT_DIR = BASE_DIR  / "output"
DB_PATH    = DATA_DIR  / "factoring_prospeccion.db"

RAW_LICITACIONES_DIR = RAW_DIR / "licitaciones"
RAW_ORDENES_DIR      = RAW_DIR / "ordenes_compra"

# ── Archivos SII ──────────────────────────────────────────────
# Nómina principal 2020-2024 (tramos ventas, capital, rubro)
SII_TXT_PATH         = RAW_DIR / "nomina_sii.txt"
# Razón social + fecha término giro (feb 2026) → vigencia actual
SII_RAZON_SOCIAL     = RAW_DIR / "nomina_sii_razon_social.txt"
# Actividades económicas vigentes (feb 2026)
SII_ACTIVIDADES      = RAW_DIR / "nomina_sii_actividades.txt"


def init_dirs():
    """Crea los directorios necesarios si no existen. Llamar desde run_pipeline.py."""
    for _dir in [RAW_LICITACIONES_DIR, RAW_ORDENES_DIR, OUTPUT_DIR]:
        _dir.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# API MERCADO PÚBLICO
# ─────────────────────────────────────────────────────────────
TICKET_API    = os.environ.get("MERCADOPUBLICO_TICKET", "")
API_BASE_URL  = "https://api.mercadopublico.cl/servicios/v1/publico"
OCDS_BASE_URL = "https://apis.mercadopublico.cl/OCDS/data"

# ─────────────────────────────────────────────────────────────
# CSV datos.chilecompra.cl
# ─────────────────────────────────────────────────────────────
CSV_SEP      = ";"
CSV_ENCODING = "latin-1"

# ─────────────────────────────────────────────────────────────
# SII — encodings confirmados
# ─────────────────────────────────────────────────────────────
SII_SEP               = "\t"
SII_ENCODING          = "utf-8-sig"   # confirmado por prueba
SII_ENCODING_FALLBACK = "latin-1"

# Cómo aparece Los Lagos en los archivos SII
REGION_LAGOS_SII = "X REGION LOS LAGOS"

# ─────────────────────────────────────────────────────────────
# PARÁMETROS EXTRACCIÓN API
# ─────────────────────────────────────────────────────────────
FECHA_INICIO_HIST = "2022-01-01"
SLEEP_OCDS        = 0.4

# ─────────────────────────────────────────────────────────────
# FILTROS DE NEGOCIO — SCORING
# ─────────────────────────────────────────────────────────────
TRAMOS_VENTAS_OK = ["2", "3", "4"]

SCORE_NIVEL_1 = 65   # bajado de 70: el modelo ahora usa más features
SCORE_NIVEL_2 = 42

# Ticket mínimo para que valga factoring (CLP)
# OC promedio menor a esto no justifica el costo de operación
TICKET_MINIMO_FACTORING = 3_000_000   # $3M CLP

# Umbral mínimo de P(win) para mostrar en alertas de pre-adjudicación
PRED_WIN_THRESHOLD = 55   # %

# Pesos del score — 12 features, deben sumar 1.0
#
# Cambios respecto a versión anterior:
#  - f_capital_negativo bajó de 20% → 7% (señal de riesgo, no solo oportunidad)
#  - f_monto_oc subió de 5% → 14%  (ticket real de factoring)
#  - f_oc_reciente subió de 5% → 11% (necesidad activa ahora)
#  - f_dias_entre_adj_oc NUEVO 10%  (ventana adj→OC = corazón del factoring)
#  - f_licitacion_grande_reciente NUEVO 8% (contrato grande reciente = urge)
#  - f_tasa_adjudicacion NUEVO 7%   (empresa que gana = flujo sostenido)
#  - f_crecimiento_oc_yoy NUEVO 7%  (empresa en expansión = mayor necesidad)
#  - f_plazo_pago_cliente NUEVO 5%  (trabaja con organismos que pagan rápido)
#  - f_volumen_oc eliminado (reemplazado por f_monto_oc, más relevante)
PESOS_SCORING = {
    "f_historial":                  0.15,
    "f_tramo_ventas":               0.10,
    "f_capital_negativo":           0.07,
    "f_monto_oc":                   0.14,
    "f_oc_reciente":                0.11,
    "f_dias_entre_adj_oc":          0.10,
    "f_licitacion_grande_reciente": 0.08,
    "f_tasa_adjudicacion":          0.07,
    "f_crecimiento_oc_yoy":         0.07,
    "f_plazo_pago_cliente":         0.05,
    "f_antiguedad":                 0.03,
    "f_rubro_prioritario":          0.03,
}

# ─────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────
EXCEL_OUTPUT = OUTPUT_DIR / "prospectos_factoring.xlsx"
