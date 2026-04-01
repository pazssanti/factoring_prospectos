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

# ─────────────────────────────────────────────────────────────
# CRM — Estados de seguimiento comercial
# ─────────────────────────────────────────────────────────────
CRM_ESTADOS = [
    "PENDIENTE",          # nunca contactado (default)
    "CONTACTADO",         # se llamó, no se profundizó
    "INTERESADO",         # mostró interés, seguimiento activo
    "PROPUESTA_ENVIADA",  # se envió propuesta comercial
    "CLIENTE",            # cliente activo de Patagonia Factoring
    "RECHAZADO",          # descartó el servicio explícitamente
    "NO_APLICA",          # no cumple criterios (micro, cerrada, etc.)
]

# ─────────────────────────────────────────────────────────────
# SCORING — Pesos por feature (14 features, suma = 1.0)
#
# v3 respecto a v2:
#  - f_concentracion_organismo NUEVO 4% (riesgo: empresa mono-cliente)
#  - f_estacionalidad          NUEVO 3% (mes con mayor gasto público)
#  - f_historial     reducido  15% → 12%
#  - f_tasa_adj      reducido  7%  → 5%
#  - f_crecimiento   reducido  7%  → 5%
# ─────────────────────────────────────────────────────────────
PESOS_SCORING = {
    "f_historial":                  0.12,
    "f_tramo_ventas":               0.10,
    "f_capital_negativo":           0.07,
    "f_monto_oc":                   0.14,
    "f_oc_reciente":                0.11,
    "f_dias_entre_adj_oc":          0.10,
    "f_licitacion_grande_reciente": 0.08,
    "f_tasa_adjudicacion":          0.05,
    "f_crecimiento_oc_yoy":         0.05,
    "f_plazo_pago_cliente":         0.05,
    "f_concentracion_organismo":    0.04,  # NUEVO — riesgo diversificación
    "f_estacionalidad":             0.03,  # NUEVO — mes de mayor gasto público
    "f_antiguedad":                 0.03,
    "f_rubro_prioritario":          0.03,
}

# ─────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────
EXCEL_OUTPUT = OUTPUT_DIR / "prospectos_factoring.xlsx"
