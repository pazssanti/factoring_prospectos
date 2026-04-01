# =============================================================
#  ingesta/mercadopublico_api.py
#
#  Extrae licitaciones y órdenes de compra desde la API
#  de Mercado Público en tiempo real.
#
#  Ticket: 3044E78B-CE32-450C-979F-7F44E1632A8E
#
#  Tablas que genera:
#    raw_licitaciones_api      — licitaciones adjudicadas
#    raw_ordenes_api           — órdenes de compra aceptadas
#    raw_licitaciones_activas  — licitaciones abiertas HOY
# =============================================================

import sys
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import TICKET_API, API_BASE_URL, DB_PATH, FECHA_INICIO_HIST
import time


# ─────────────────────────────────────────────────────────────
# LLAMADA HTTP BASE
# ─────────────────────────────────────────────────────────────

def _api_get(endpoint: str, params: dict) -> dict | None:
    params["ticket"] = TICKET_API
    url = f"{API_BASE_URL}/{endpoint}.json"
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if "Listado" in data:
                return data
            return None
        if r.status_code == 401:
            print("  [ERROR 401] Ticket inválido o expirado.")
            return None
        print(f"  [HTTP {r.status_code}] {url}")
        return None
    except requests.exceptions.Timeout:
        print(f"  [TIMEOUT] {endpoint} — reintentando...")
        time.sleep(2)
        try:
            r = requests.get(url, params=params, timeout=20)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None
    except Exception as e:
        print(f"  [ERROR] {endpoint}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# PARSERS
# ─────────────────────────────────────────────────────────────

def _parsear_licitacion(item: dict, fecha_str: str) -> dict:
    return {
        "codigo_externo":   item.get("CodigoExterno"),
        "nombre":           item.get("Nombre"),
        "codigo_estado":    item.get("CodigoEstado"),
        "fecha_cierre":     item.get("FechaCierre"),
        "codigo_organismo": item.get("CodigoOrganismo"),
        "nombre_organismo": item.get("NombreOrganismo"),
        "monto_estimado":   item.get("MontoEstimado"),
        "tipo":             item.get("Tipo"),
        "region":           item.get("Region"),
        "fecha_api":        fecha_str,
        "fecha_extraccion": datetime.now().isoformat(),
    }


def _parsear_oc(item: dict, fecha_str: str) -> dict:
    return {
        "codigo_oc":        item.get("Codigo"),
        "nombre":           item.get("Nombre"),
        "codigo_organismo": item.get("CodigoOrganismo"),
        "nombre_organismo": item.get("NombreOrganismo"),
        "codigo_proveedor": item.get("CodigoProveedor"),
        "nombre_proveedor": item.get("NombreProveedor"),
        "monto":            item.get("Monto"),
        "fecha_envio":      item.get("FechaEnvio"),
        "estado":           item.get("Estado"),
        "region":           item.get("Region"),
        "fecha_api":        fecha_str,
        "fecha_extraccion": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# EXTRACCIÓN POR FECHA
# ─────────────────────────────────────────────────────────────

def extraer_licitaciones_fecha(fecha_ddmmyyyy: str) -> list:
    data = _api_get("licitaciones", {
        "fecha": fecha_ddmmyyyy,
        "estado": "adjudicada"
    })
    if not data:
        return []
    return [_parsear_licitacion(i, fecha_ddmmyyyy)
            for i in data["Listado"]]


def extraer_ordenes_fecha(fecha_ddmmyyyy: str) -> list:
    data = _api_get("ordenesdecompra", {
        "fecha": fecha_ddmmyyyy,
        "estado": "aceptada"
    })
    if not data:
        return []
    return [_parsear_oc(i, fecha_ddmmyyyy) for i in data["Listado"]]


def extraer_licitaciones_activas() -> list:
    data = _api_get("licitaciones", {"estado": "publicada"})
    if not data:
        return []
    return [_parsear_licitacion(i, datetime.now().strftime("%d%m%Y"))
            for i in data["Listado"]]


# ─────────────────────────────────────────────────────────────
# LOOP POR RANGO
# ─────────────────────────────────────────────────────────────

def extraer_rango(fecha_inicio: str, fecha_fin: str) -> tuple:
    inicio = datetime.strptime(fecha_inicio, "%Y-%m-%d")
    fin    = datetime.strptime(fecha_fin,    "%Y-%m-%d")
    total  = (fin - inicio).days + 1

    todas_lit = []
    todas_oc  = []
    actual    = inicio
    dia_n     = 0

    while actual <= fin:
        fecha_api = actual.strftime("%d%m%Y")
        todas_lit.extend(extraer_licitaciones_fecha(fecha_api))
        todas_oc.extend(extraer_ordenes_fecha(fecha_api))
        dia_n += 1

        if dia_n % 30 == 0:
            pct = round(100 * dia_n / total, 1)
            print(f"  [{pct}%] {dia_n}/{total} días — "
                  f"{len(todas_lit)} lit, {len(todas_oc)} OC")

        actual += timedelta(days=1)
        time.sleep(0.25)

    return todas_lit, todas_oc


# ─────────────────────────────────────────────────────────────
# PERSISTENCIA
# ─────────────────────────────────────────────────────────────

def _guardar(df: pd.DataFrame, tabla: str,
             pk: str, conn: sqlite3.Connection):
    if df.empty:
        return
    df.to_sql(f"{tabla}_tmp", conn, if_exists="replace", index=False)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tabla} AS
        SELECT * FROM {tabla}_tmp WHERE 0
    """)
    try:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS "
            f"idx_{tabla}_{pk} ON {tabla}({pk})"
        )
    except Exception:
        pass
    cols = ", ".join([f'"{c}"' for c in df.columns])
    conn.execute(f"""
        INSERT OR IGNORE INTO {tabla} ({cols})
        SELECT {cols} FROM {tabla}_tmp
    """)
    conn.execute(f"DROP TABLE IF EXISTS {tabla}_tmp")
    conn.commit()
    n = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    print(f"  {tabla}: {n:,} registros")


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run(modo: str = "incremental"):
    """
    modo='full'        → desde FECHA_INICIO_HIST hasta hoy
    modo='incremental' → últimas 48h (uso diario)
    modo='activas'     → solo licitaciones publicadas ahora
    """
    print("=" * 55)
    print(f"ingesta/mercadopublico_api.py — modo={modo}")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    hoy  = datetime.now()

    if modo == "full":
        fecha_ini = FECHA_INICIO_HIST
        fecha_fin = hoy.strftime("%Y-%m-%d")
        print(f"Extrayendo {fecha_ini} → {fecha_fin}")
        print("AVISO: para el histórico usar los CSV es más rápido.")
        lit, oc = extraer_rango(fecha_ini, fecha_fin)
        _guardar(pd.DataFrame(lit), "raw_licitaciones_api",
                 "codigo_externo", conn)
        _guardar(pd.DataFrame(oc),  "raw_ordenes_api",
                 "codigo_oc", conn)

    elif modo == "incremental":
        ayer    = (hoy - timedelta(days=1)).strftime("%Y-%m-%d")
        hoy_str = hoy.strftime("%Y-%m-%d")
        print(f"Extrayendo {ayer} -- {hoy_str}")
        lit, oc = extraer_rango(ayer, hoy_str)
        _guardar(pd.DataFrame(lit), "raw_licitaciones_api",
                 "codigo_externo", conn)
        _guardar(pd.DataFrame(oc),  "raw_ordenes_api",
                 "codigo_oc", conn)

    elif modo == "activas":
        print("Extrayendo licitaciones publicadas ahora...")
        activas = extraer_licitaciones_activas()
        df_act  = pd.DataFrame(activas)
        if not df_act.empty:
            df_act.to_sql("raw_licitaciones_activas", conn,
                          if_exists="replace", index=False)
            conn.commit()
            print(f"  raw_licitaciones_activas: {len(df_act):,} registros")

    # Resumen tablas
    print("\nEstado tablas API:")
    for t in ["raw_licitaciones_api", "raw_ordenes_api",
              "raw_licitaciones_activas"]:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n:,} registros")
        except Exception:
            print(f"  {t}: no existe aún")

    conn.close()
    print("\ningesta/mercadopublico_api.py completado.")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    run(modo)