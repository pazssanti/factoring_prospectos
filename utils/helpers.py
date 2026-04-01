# =============================================================
#  utils/helpers.py — funciones compartidas del proyecto
#
#  Exports:
#    mapa_tramo         — dict global de tramos de ventas SII
#    normalizar_rut()   — normaliza un RUT individual (str → str)
#    normalizar_rut_serie() — versión vectorizada para DataFrames
#    get_con_reintento() — GET HTTP con backoff exponencial
# =============================================================

import logging
import time
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# TRAMOS DE VENTAS SII
# ─────────────────────────────────────────────────────────────
# Tramos de ventas SII — Chile (granularidad real del archivo de nómina)
# Tramos 1-13 ordenados de menor a mayor volumen de ventas anuales.
# Para factoring, el sweet spot es tramos 2-4 (PyME con flujo real de OC).
mapa_tramo: dict = {
    "1":  "Micro (sin ventas)",
    "2":  "Micro",
    "3":  "Pequeña baja",
    "4":  "Pequeña",
    "5":  "Pequeña alta",
    "6":  "Mediana baja",
    "7":  "Mediana",
    "8":  "Mediana alta",
    "9":  "Grande",
    "10": "Grande alta",
    "11": "Muy grande",
    "12": "Corporativo",
    "13": "Holding",
}


# ─────────────────────────────────────────────────────────────
# NORMALIZACIÓN DE RUT
# ─────────────────────────────────────────────────────────────

def normalizar_rut(rut: str, dv: str = None) -> str:
    """
    Normaliza un RUT individual: elimina puntos y guiones, mayúsculas.

    Args:
        rut: número de RUT (con o sin puntos/guión)
        dv:  dígito verificador separado (opcional)

    Returns:
        RUT normalizado en mayúsculas, sin puntos ni guiones.
        Si se pasa dv, lo concatena al final.

    Ejemplo:
        normalizar_rut("12.345.678-9")   → "123456789"
        normalizar_rut("12345678", "9")  → "123456789"
    """
    r = str(rut).strip().replace(".", "").replace("-", "").upper()
    if dv is not None:
        d = str(dv).strip().upper()
        return r + d
    return r


def normalizar_rut_serie(
    rut: pd.Series, dv: pd.Series = None
) -> pd.Series:
    """
    Versión vectorizada de normalizar_rut para usar con DataFrames.

    Args:
        rut: Serie con RUTs (puede tener NaN)
        dv:  Serie con dígitos verificadores separados (opcional)

    Returns:
        Serie con RUTs normalizados.
    """
    r = (
        rut.fillna("").astype(str).str.strip()
        .str.replace(".", "", regex=False)
        .str.replace("-", "", regex=False)
        .str.upper()
    )
    if dv is not None:
        d = dv.fillna("").astype(str).str.strip().str.upper()
        return r + d
    return r


# ─────────────────────────────────────────────────────────────
# HTTP CON REINTENTOS Y BACKOFF EXPONENCIAL
# ─────────────────────────────────────────────────────────────

def get_con_reintento(
    url: str,
    params: dict = None,
    intentos: int = 5,
    espera: int = 30,
) -> Optional[requests.Response]:
    """
    GET HTTP con backoff exponencial entre reintentos.

    Esperas sucesivas: espera, espera*2, espera*4, ...
    Retorna la Response si status 200, None si agota todos los intentos.

    Códigos que NO reintentan:
        401 — ticket inválido o expirado (error permanente)

    Args:
        url:      URL completa del endpoint
        params:   parámetros de query string (dict, opcional)
        intentos: número máximo de intentos (default 5)
        espera:   segundos de espera base entre reintentos (default 30)

    Returns:
        requests.Response con status 200, o None.
    """
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, params=params, timeout=30)

            if r.status_code == 200:
                return r

            if r.status_code == 401:
                logger.error(
                    "HTTP 401 — ticket inválido o expirado: %s", url
                )
                return None

            logger.warning(
                "HTTP %s en intento %d/%d: %s",
                r.status_code, intento, intentos, url,
            )

        except requests.exceptions.Timeout:
            logger.warning(
                "Timeout en intento %d/%d: %s", intento, intentos, url
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "Error en intento %d/%d: %s — %s",
                intento, intentos, url, exc,
            )

        if intento < intentos:
            pausa = espera * (2 ** (intento - 1))
            logger.info("Reintentando en %d s...", pausa)
            time.sleep(pausa)

    logger.error("Agotados %d intentos para: %s", intentos, url)
    return None
