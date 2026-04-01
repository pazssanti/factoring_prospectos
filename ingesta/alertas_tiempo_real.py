# =============================================================
#  ingesta/alertas_tiempo_real.py — Versión 3
#
#  Detecta y alerta en 3 ventanas de oportunidad:
#    1. OC emitidas hoy    — flujo original mejorado
#    2. Adjudicadas sin OC — ventana adj→OC (empresa ya sabe que ganó)
#    3. Predecir Ganadores — P(win) > 60% antes del cierre
#
#  Inputs:  DB SQLite + API Mercado Público
#  Outputs: output/alertas_adjudicaciones.xlsx (3 hojas)
# =============================================================

import sys
import time
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import TICKET_API, API_BASE_URL, DB_PATH, DATA_DIR, OUTPUT_DIR, init_dirs
from utils.helpers import normalizar_rut, mapa_tramo, get_con_reintento

init_dirs()
ULTIMO_CHECK = DATA_DIR / "ultimo_check_alertas.txt"
ALERTAS_LOG  = OUTPUT_DIR / "alertas_adjudicaciones.xlsx"


# ─────────────────────────────────────────────────────────────
# UTILIDAD COMÚN
# ─────────────────────────────────────────────────────────────

def calcular_accion(monto_millones: float, dias: int) -> str:
    """
    LLAMAR HOY         — monto > $50M y dias_desde_adjudicacion <= 3
    LLAMAR ESTA SEMANA — monto > $20M o dias_desde_adjudicacion <= 7
    MONITOREAR         — resto
    """
    if monto_millones > 50 and dias <= 3:
        return "LLAMAR HOY"
    if monto_millones > 20 or dias <= 7:
        return "LLAMAR ESTA SEMANA"
    return "MONITOREAR"


# ─────────────────────────────────────────────────────────────
# DATOS BASE: SII + SCORING
# ─────────────────────────────────────────────────────────────

def get_organismos_lagos(conn: sqlite3.Connection) -> set:
    try:
        rows = conn.execute("""
            SELECT DISTINCT codigounidadcompra
            FROM raw_ordenes_csv
            WHERE LOWER(regionunidadcompra) LIKE '%lagos%'
            AND codigounidadcompra IS NOT NULL
            AND codigounidadcompra != ''
        """).fetchall()
        return {str(r[0]).strip() for r in rows}
    except Exception as e:
        print(f"  Error organismos Lagos: {e}")
        return set()


def get_datos_sii(rut_norm: str, conn: sqlite3.Connection) -> dict:
    try:
        row = conn.execute("""
            SELECT razon_social, comuna, tramo_ventas,
                   tramo_capital_negativo, actividad_economica,
                   num_trabajadores, vigente_2026
            FROM raw_empresas_sii
            WHERE rut_normalizado = ?
        """, (rut_norm,)).fetchone()
        if row:
            return {
                "en_sii":           True,
                "razon_social_sii": row[0],
                "comuna_sii":       row[1],
                "tramo_ventas":     mapa_tramo.get(str(row[2]).strip(), str(row[2])),
                "capital_negativo": bool(
                    row[3] and str(row[3]).strip()
                    not in ("", "0", "nan", "None")
                ),
                "actividad":        row[4],
                "trabajadores":     row[5],
                "vigente_2026":     row[6] == 1,
            }
    except Exception:
        pass
    return {"en_sii": False}


def get_datos_prospecto(rut_norm: str, conn: sqlite3.Connection) -> dict:
    """Score, nivel y probabilidad desde tablas de scoring."""
    result = {
        "score_prospecto":          None,
        "nivel_prospecto":          None,
        "probabilidad_adjudicacion": None,
    }
    try:
        row = conn.execute("""
            SELECT pr.score, pr.nivel, pa.probabilidad_adjudicacion
            FROM prospectos_rankeados pr
            LEFT JOIN predicciones_activas pa
                ON pr.rut_normalizado = pa.rut_normalizado
            WHERE pr.rut_normalizado = ?
        """, (rut_norm,)).fetchone()
        if row:
            result["score_prospecto"]           = row[0]
            result["nivel_prospecto"]            = row[1]
            result["probabilidad_adjudicacion"]  = row[2]
    except Exception:
        pass
    return result


# ─────────────────────────────────────────────────────────────
# FLUJO 1 — ALERTAS OC (emitidas hoy)
# ─────────────────────────────────────────────────────────────

def get_listado_oc(fecha_ddmmyyyy: str) -> list:
    # intentos=1 para fallar rápido — el flujo DB es la fuente primaria
    r = get_con_reintento(
        f"{API_BASE_URL}/ordenesdecompra.json",
        {"ticket": TICKET_API, "fecha": fecha_ddmmyyyy, "estado": "aceptada"},
        intentos=1,
        espera=5,
    )
    if r is None:
        return []
    try:
        return r.json().get("Listado", [])
    except Exception:
        return []


def filtrar_oc_lagos(listado: list, codigos_lagos: set) -> list:
    """
    El código de OC tiene formato ORGANISMO-NUMERO-TIPO.
    Filtra solo las OC de organismos de Los Lagos.
    """
    filtradas = []
    for oc in listado:
        partes = str(oc.get("Codigo", "")).split("-")
        if partes and partes[0] in codigos_lagos:
            filtradas.append(oc)
    return filtradas


def get_detalle_oc(codigo_oc: str) -> dict | None:
    r = get_con_reintento(
        f"{API_BASE_URL}/ordenesdecompra.json",
        {"ticket": TICKET_API, "codigo": codigo_oc},
        intentos=3,
        espera=5,
    )
    if r is None:
        return None
    try:
        listado = r.json().get("Listado", [])
        return listado[0] if listado else None
    except Exception:
        return None


def construir_alerta_oc(detalle: dict, datos_sii: dict,
                        datos_pros: dict) -> dict:
    proveedor = detalle.get("Proveedor", {})
    comprador = detalle.get("Comprador", {})
    fechas    = detalle.get("Fechas", {})
    hoy       = datetime.now()

    monto = 0.0
    try:
        monto = float(str(detalle.get("Total", 0)).replace(",", "."))
    except Exception:
        pass
    monto_mm = round(monto / 1_000_000, 2)

    fecha_ace_str = str(fechas.get("FechaAceptacion", ""))[:10]
    try:
        dias_oc = (hoy - datetime.strptime(fecha_ace_str, "%Y-%m-%d")).days
    except Exception:
        dias_oc = 0

    prob = datos_pros.get("probabilidad_adjudicacion")

    argumento = ""
    if datos_sii.get("en_sii"):
        argumento = (
            f"OC ${monto_mm}M con "
            f"{comprador.get('NombreOrganismo', 'el Estado')}. "
            f"Paga a 30 días. "
        )
        if datos_sii.get("capital_negativo"):
            argumento += "Capital negativo. "
        argumento += "Llamar HOY."

    return {
        "accion_recomendada": calcular_accion(monto_mm, dias_oc),
        "hora_deteccion":     hoy.strftime("%d/%m/%Y %H:%M"),
        "codigo_oc":          detalle.get("Codigo"),
        "nombre_oc":          str(detalle.get("Nombre", ""))[:60],
        "organismo":          comprador.get("NombreOrganismo"),
        "comuna_organismo":   comprador.get("ComunaUnidad"),
        "monto_MM":           monto_mm,
        "fecha_aceptacion":   fecha_ace_str,
        "rut_proveedor":      proveedor.get("RutSucursal", ""),
        "empresa":            datos_sii.get("razon_social_sii") or proveedor.get("Nombre"),
        "ciudad":             datos_sii.get("comuna_sii") or comprador.get("ComunaUnidad"),
        "tamaño_empresa":     datos_sii.get("tramo_ventas", ""),
        "capital_negativo":   "SÍ" if datos_sii.get("capital_negativo") else "No",
        "en_sii":             "SÍ ★" if datos_sii.get("en_sii") else "No",
        "vigente_2026":       "SÍ" if datos_sii.get("vigente_2026") else "No",
        "score_prospecto":    datos_pros.get("score_prospecto"),
        "nivel_prospecto":    datos_pros.get("nivel_prospecto"),
        "P_ganar_licit":      f"{prob:.0f}%" if prob is not None else "-",
        "argumento_llamada":  argumento,
    }


def procesar_oc_desde_api_db(conn: sqlite3.Connection,
                              dias: int = 7) -> list:
    """
    Lee OC desde raw_ordenes_api — datos descargados cuando la API estuvo activa.
    Cubre el gap entre el último CSV (~15 días de lag) y hoy.
    Solo tiene info básica (sin RUT detallado), se enriquece con SII por nombre.
    """
    try:
        df = pd.read_sql(f"""
            SELECT
                o.codigo_oc,
                o.nombre,
                o.nombre_organismo   AS organismo,
                o.nombre_proveedor,
                o.monto              AS monto_raw,
                o.fecha_envio        AS fecha_aceptacion,
                o.region,
                o.fecha_api
            FROM raw_ordenes_api o
            WHERE o.fecha_api >= date('now', '-{dias} days')
            AND (LOWER(o.region) LIKE '%lagos%' OR o.region IS NULL)
            AND CAST(o.monto AS REAL) >= 1000000
            AND CAST(o.monto AS REAL) <= 5000000000
            ORDER BY CAST(o.monto AS REAL) DESC
        """, conn)
    except Exception:
        return []

    if df.empty:
        return []

    hoy     = datetime.now()
    alertas = []

    for _, row in df.iterrows():
        monto    = float(row.get("monto_raw") or 0)
        monto_mm = round(monto / 1_000_000, 2)

        fecha_str = str(row.get("fecha_aceptacion") or "")[:10]
        try:
            dias_oc = (hoy - datetime.strptime(fecha_str, "%Y-%m-%d")).days
        except Exception:
            dias_oc = 0

        alertas.append({
            "accion_recomendada": calcular_accion(monto_mm, dias_oc),
            "fuente":             "API descargada",
            "hora_deteccion":     hoy.strftime("%d/%m/%Y %H:%M"),
            "codigo_oc":          row.get("codigo_oc"),
            "nombre_oc":          str(row.get("nombre") or "")[:60],
            "organismo":          row.get("organismo"),
            "comuna_organismo":   "-",
            "monto_MM":           monto_mm,
            "fecha_aceptacion":   fecha_str,
            "dias_desde_oc":      dias_oc,
            "rut_proveedor":      "-",
            "empresa":            row.get("nombre_proveedor"),
            "ciudad":             "-",
            "tamanio_empresa":    "-",
            "capital_negativo":   "-",
            "en_sii":             "No verificado",
            "vigente_2026":       "-",
            "score_prospecto":    None,
            "nivel_prospecto":    "-",
            "urgencia":           "-",
            "P_ganar_licit":      "-",
            "argumento_llamada":  f"OC ${monto_mm}M con {row.get('organismo','el Estado')}. Verificar en SII.",
        })

    return alertas


def procesar_oc_desde_db(conn: sqlite3.Connection,
                         dias: int = 30) -> list:
    """
    Lee OC recientes desde clean_ordenes + enriquece con SII y scoring en
    una sola query SQL (sin loops por fila — mucho más rápido).
    No depende de la API. Ventana por defecto: 30 días (cubre lag CSV ~15d).
    """
    try:
        df = pd.read_sql(f"""
            SELECT
                o.codigo                                    AS codigo_oc,
                o.nombre                                    AS nombre_oc,
                o.organismopublico                          AS organismo,
                o.ciudadunidadcompra                        AS comuna_organismo,
                o.monto_oc_clp                              AS monto_raw,
                o.fechaaceptacion                           AS fecha_aceptacion,
                o.rut_proveedor_norm                        AS rut_proveedor,
                o.nombreproveedor                           AS nombre_proveedor,
                o.comunaproveedor                           AS ciudad_proveedor,
                o.rubron1                                   AS rubro,
                -- SII
                s.razon_social                              AS razon_social_sii,
                s.comuna                                    AS comuna_sii,
                s.tramo_ventas,
                s.tramo_capital_negativo,
                s.vigente_2026,
                -- Scoring
                pr.score                                    AS score_prospecto,
                pr.nivel                                    AS nivel_prospecto,
                pr.urgencia_contacto,
                pa.probabilidad_adjudicacion                AS P_win
            FROM clean_ordenes o
            LEFT JOIN raw_empresas_sii s
                ON o.rut_proveedor_norm = s.rut_normalizado
            LEFT JOIN prospectos_rankeados pr
                ON o.rut_proveedor_norm = pr.rut_normalizado
            LEFT JOIN predicciones_activas pa
                ON o.rut_proveedor_norm = pa.rut_normalizado
            WHERE o.es_aceptada = 1
            AND o.fechaaceptacion >= date('now', '-{dias} days')
            AND LOWER(o.regionunidadcompra) LIKE '%lagos%'
            AND o.monto_oc_clp BETWEEN 1000000 AND 5000000000
            ORDER BY o.monto_oc_clp DESC
        """, conn)
    except Exception as exc:
        print(f"  Error leyendo clean_ordenes: {exc}")
        return []

    if df.empty:
        return []

    hoy     = datetime.now()
    alertas = []

    for _, row in df.iterrows():
        monto    = float(row.get("monto_raw") or 0)
        monto_mm = round(monto / 1_000_000, 2)
        en_sii   = pd.notna(row.get("razon_social_sii"))

        # Filtrar: solo empresas SII conocidas o monto > $5M
        if not en_sii and monto < 5_000_000:
            continue

        fecha_str = str(row.get("fecha_aceptacion") or "")[:10]
        try:
            dias_oc = (hoy - datetime.strptime(fecha_str, "%Y-%m-%d")).days
        except Exception:
            dias_oc = 0

        cap_neg  = pd.notna(row.get("tramo_capital_negativo")) and \
                   str(row.get("tramo_capital_negativo")).strip() not in ("", "0", "nan", "None")
        prob     = row.get("P_win")
        tramo    = mapa_tramo.get(str(row.get("tramo_ventas", "")).strip(), "")

        argumento = ""
        if en_sii:
            argumento = (
                f"OC ${monto_mm}M con {row.get('organismo','el Estado')}. "
                f"Paga a 30 dias. "
            )
            if cap_neg:
                argumento += "Capital negativo. "
            argumento += "Llamar HOY." if dias_oc <= 3 else "Llamar esta semana."

        alertas.append({
            "accion_recomendada": calcular_accion(monto_mm, dias_oc),
            "fuente":             "DB local",
            "hora_deteccion":     hoy.strftime("%d/%m/%Y %H:%M"),
            "codigo_oc":          row.get("codigo_oc"),
            "nombre_oc":          str(row.get("nombre_oc") or "")[:60],
            "organismo":          row.get("organismo"),
            "comuna_organismo":   row.get("comuna_organismo"),
            "monto_MM":           monto_mm,
            "fecha_aceptacion":   fecha_str,
            "dias_desde_oc":      dias_oc,
            "rut_proveedor":      str(row.get("rut_proveedor") or ""),
            "empresa":            row.get("razon_social_sii") or row.get("nombre_proveedor"),
            "ciudad":             row.get("comuna_sii") or row.get("ciudad_proveedor"),
            "tamanio_empresa":    tramo,
            "capital_negativo":   "SI" if cap_neg else "No",
            "en_sii":             "SI *" if en_sii else "No",
            "vigente_2026":       "SI" if row.get("vigente_2026") == 1 else "No",
            "score_prospecto":    row.get("score_prospecto"),
            "nivel_prospecto":    row.get("nivel_prospecto"),
            "urgencia":           row.get("urgencia_contacto"),
            "P_ganar_licit":      f"{prob:.0f}%" if pd.notna(prob) else "-",
            "argumento_llamada":  argumento,
        })

    return alertas


def procesar_oc_api(conn: sqlite3.Connection,
                    codigos_lagos: set) -> list:
    """
    Intenta descargar OC de HOY desde la API.
    Solo se llama si la API está activa — bonus sobre los datos DB.
    Retorna lista vacía si la API falla (sin romper el flujo).
    """
    ahora   = datetime.now()
    fechas  = [
        ahora.strftime("%d%m%Y"),
        (ahora - timedelta(days=1)).strftime("%d%m%Y"),
    ]
    alertas = []

    for fecha_str in fechas:
        fecha_leg = datetime.strptime(fecha_str, "%d%m%Y").strftime("%d/%m/%Y")
        listado   = get_listado_oc(fecha_str)
        if not listado:
            continue

        oc_lagos = filtrar_oc_lagos(listado, codigos_lagos)
        print(f"    API [{fecha_leg}]: {len(oc_lagos)} OC Los Lagos")

        for oc in oc_lagos[:30]:
            codigo  = oc.get("Codigo")
            detalle = get_detalle_oc(codigo)
            if not detalle:
                continue

            rut_raw  = detalle.get("Proveedor", {}).get("RutSucursal", "")
            rut_norm = normalizar_rut(rut_raw)
            datos_sii = get_datos_sii(rut_norm, conn)

            monto = 0.0
            try:
                monto = float(str(detalle.get("Total", 0)).replace(",", "."))
            except Exception:
                pass

            if datos_sii.get("en_sii") or monto > 50_000_000:
                datos_pros = get_datos_prospecto(rut_norm, conn)
                alertas.append(
                    construir_alerta_oc(detalle, datos_sii, datos_pros)
                )
            time.sleep(0.3)

    return alertas


def procesar_oc_dia(conn: sqlite3.Connection,
                    codigos_lagos: set) -> list:
    """
    Estrategia DB-first:
      1. Lee OC de los últimos 7 días desde clean_ordenes (siempre disponible)
      2. Intenta enriquecer con OC del día desde API (bonus, puede fallar)
      3. Deduplica por codigo_oc para no mostrar dos veces la misma
    """
    # FUENTE 1: CSV histórico en DB (hasta ~15 dias de lag, siempre disponible)
    print("  [1] CSV historico (ultimos 30 dias)...")
    alertas_db = procesar_oc_desde_db(conn, dias=30)
    print(f"      {len(alertas_db)} OC")

    # FUENTE 2: raw_ordenes_api (descargadas cuando la API estuvo activa)
    print("  [2] raw_ordenes_api (descargadas previamente)...")
    alertas_api_db = procesar_oc_desde_api_db(conn, dias=7)
    print(f"      {len(alertas_api_db)} OC")

    # FUENTE 3: API en vivo (solo para OC de HOY, puede fallar)
    alertas_api: list = []
    if TICKET_API:
        print("  [3] API en vivo (OC de hoy)...")
        try:
            alertas_api = procesar_oc_api(conn, codigos_lagos)
            print(f"      {len(alertas_api)} OC")
        except Exception as exc:
            print(f"      no disponible ({exc.__class__.__name__})")

    # Unir y deduplicar por codigo_oc (prioridad: DB enriquecida > API_DB > API viva)
    todos = alertas_db + alertas_api_db + alertas_api
    vistos: set = set()
    resultado: list = []
    for a in todos:
        key = str(a.get("codigo_oc") or "")
        if key and key not in vistos:
            vistos.add(key)
            resultado.append(a)
        elif not key:
            resultado.append(a)

    return resultado


# ─────────────────────────────────────────────────────────────
# FLUJO 2 — ALERTAS ADJUDICACIÓN (sin OC aún)
# ─────────────────────────────────────────────────────────────

def get_adjudicadas_sin_oc(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Detecta adjudicaciones recientes sin OC emitida todavía.
    Fuente 1: raw_licitaciones_activas (CodigoEstado = adjudicada)
    Fuente 2: clean_licitaciones (últimos 14 días, con RUT proveedor)
    """
    hoy = pd.Timestamp(datetime.now())
    frames = []

    # ── raw_licitaciones_activas ──────────────────────────────
    n_act_adjudicadas = 0
    try:
        df_act = pd.read_sql("""
            SELECT
                codigo_externo   AS codigo_licitacion,
                nombre           AS nombre_licitacion,
                nombre_organismo AS organismo,
                monto_estimado   AS monto_raw,
                region,
                fecha_cierre     AS fecha_adjudicacion,
                NULL             AS rut_proveedor
            FROM raw_licitaciones_activas
            WHERE LOWER(codigo_estado) LIKE '%adjudic%'
        """, conn)
        n_act_adjudicadas = len(df_act)
        if not df_act.empty:
            frames.append(df_act)
        else:
            # Tabla existe pero sin adjudicadas: puede ser API stale
            n_total_act = conn.execute(
                "SELECT COUNT(*) FROM raw_licitaciones_activas"
            ).fetchone()[0]
            if n_total_act == 0:
                print("  AVISO: raw_licitaciones_activas vacia — "
                      "la API puede no haberse ejecutado hoy")
            else:
                print(f"  AVISO: {n_total_act} licitaciones activas pero "
                      "ninguna con estado adjudicada")
    except Exception:
        pass

    # ── clean_licitaciones (tiene RUT ganador) — ventana 30 días ─
    try:
        df_clean = pd.read_sql("""
            SELECT
                codigoexterno           AS codigo_licitacion,
                nombre                  AS nombre_licitacion,
                nombreorganismo         AS organismo,
                monto_total_adjudicado  AS monto_raw,
                regionunidad            AS region,
                fechaadjudicacion       AS fecha_adjudicacion,
                rut_proveedor_norm      AS rut_proveedor
            FROM clean_licitaciones
            WHERE es_adjudicada = 1
            AND fechaadjudicacion >= date('now', '-30 days')
        """, conn)
        if not df_clean.empty:
            frames.append(df_clean)
    except Exception as exc:
        print(f"  adjudicadas fallback: {exc}")

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True).drop_duplicates("codigo_licitacion")

    # ── Excluir las que YA tienen OC emitida ─────────────────
    try:
        df_oc = pd.read_sql("""
            SELECT DISTINCT UPPER(TRIM(codigolicitacion)) AS cod
            FROM clean_ordenes
            WHERE codigolicitacion IS NOT NULL AND codigolicitacion != ''
        """, conn)
        codigos_con_oc = set(df_oc["cod"].tolist())
        df = df[
            ~df["codigo_licitacion"].astype(str).str.strip().str.upper()
            .isin(codigos_con_oc)
        ]
    except Exception:
        pass

    if df.empty:
        return df

    # ── dias_desde_adjudicacion ───────────────────────────────
    df["fecha_adj_dt"] = pd.to_datetime(df["fecha_adjudicacion"], errors="coerce")
    df["dias_desde_adjudicacion"] = (
        (hoy - df["fecha_adj_dt"]).dt.days.fillna(0).clip(0).astype(int)
    )

    # ── monto_MM ──────────────────────────────────────────────
    df["monto_MM"] = (
        pd.to_numeric(
            df["monto_raw"].astype(str).str.replace(",", ".", regex=False),
            errors="coerce"
        ).fillna(0) / 1_000_000
    ).round(2)

    return df


def enriquecer_adjudicadas(df: pd.DataFrame,
                           conn: sqlite3.Connection) -> pd.DataFrame:
    """Agrega score, nivel, probabilidad y accion_recomendada."""
    if df.empty:
        return df

    filas = []
    for _, row in df.iterrows():
        rut_norm  = str(row.get("rut_proveedor") or "").strip()
        datos_sii = get_datos_sii(rut_norm, conn) if rut_norm else {"en_sii": False}
        datos_pros = get_datos_prospecto(rut_norm, conn) if rut_norm else {}

        monto_mm = float(row.get("monto_MM", 0) or 0)
        dias     = int(row.get("dias_desde_adjudicacion", 0) or 0)
        prob     = datos_pros.get("probabilidad_adjudicacion")

        filas.append({
            "accion_recomendada":      calcular_accion(monto_mm, dias),
            "codigo_licitacion":       row.get("codigo_licitacion"),
            "nombre_licitacion":       str(row.get("nombre_licitacion", ""))[:60],
            "organismo":               row.get("organismo"),
            "region":                  row.get("region"),
            "monto_licitacion_MM":     round(monto_mm, 2),
            "dias_desde_adjudicacion": dias,
            "fecha_adjudicacion":      str(row.get("fecha_adjudicacion", ""))[:10],
            "rut_proveedor":           rut_norm,
            "empresa":                 datos_sii.get("razon_social_sii", ""),
            "ciudad":                  datos_sii.get("comuna_sii", ""),
            "tamaño_empresa":          datos_sii.get("tramo_ventas", ""),
            "capital_negativo":        "SÍ" if datos_sii.get("capital_negativo") else "No",
            "en_sii":                  "SÍ ★" if datos_sii.get("en_sii") else "No",
            "score_prospecto":         datos_pros.get("score_prospecto"),
            "nivel_prospecto":         datos_pros.get("nivel_prospecto"),
            "P_ganar_licit":           f"{prob:.0f}%" if prob is not None else "-",
        })

    return (
        pd.DataFrame(filas)
        .sort_values("monto_licitacion_MM", ascending=False)
        .reset_index(drop=True)
    )


# ─────────────────────────────────────────────────────────────
# FLUJO 3 — PREDECIR GANADORES (contactar ANTES del cierre)
# ─────────────────────────────────────────────────────────────

def get_predecir_ganadores(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Empresas con alta P(win) en licitaciones activas.

    Prioridad de fuentes:
      1. predicciones_licitaciones_lagos — generada por enriquecer_licitaciones_activas.py
         con licitaciones genuinamente abiertas (estado=publicada desde la API).
         Umbral: P_win >= 40% (max observado ~50%).
      2. predicciones_activas — fallback generado por prediccion_adjudicacion.py
         en el pipeline. Fuente: clean_licitaciones con fechaadjudicacion futura
         (puede tener lag CSV ~13 días). Umbral: > 60%.
    """
    # ── Fuente 1: licitaciones abiertas reales (API) ──────────
    try:
        df = pd.read_sql("""
            SELECT
                rut_normalizado                         AS rut,
                razon_social                            AS empresa,
                P_win                                   AS P_win_raw,
                score_prospecto,
                nivel_prospecto,
                mejor_licitacion                        AS licitacion_activa,
                organismo,
                CAST(monto_estimado_MM AS REAL)         AS monto_hist_oc_MM,
                fecha_cierre,
                dias_para_cierre,
                accion                                  AS accion_recomendada
            FROM predicciones_licitaciones_lagos
            WHERE P_win >= 40
              AND (fecha_cierre IS NULL OR fecha_cierre >= date('now'))
            ORDER BY P_win DESC
        """, conn)

        if not df.empty:
            df["P_win_pct"] = df["P_win_raw"].apply(
                lambda x: f"{x:.0f}%" if pd.notna(x) else "-"
            )
            df["fuente"] = "API — licitaciones abiertas"
            df.drop(columns=["P_win_raw"], inplace=True)
            return df
    except Exception as exc:
        print(f"  predicciones_licitaciones_lagos no disponible: {exc}")

    # ── Fuente 2: predicciones_activas (fallback, lag CSV) ────
    try:
        df = pd.read_sql("""
            SELECT
                pa.rut_normalizado                          AS rut,
                pr.razon_social                             AS empresa,
                pa.probabilidad_adjudicacion                AS P_win_raw,
                pr.score                                    AS score_prospecto,
                pr.nivel                                    AS nivel_prospecto,
                pr.urgencia_contacto                        AS accion_recomendada,
                ROUND(pr.monto_total_oc / 1000000.0, 1)    AS monto_hist_oc_MM,
                pr.licitaciones_ganadas,
                pr.ultima_oc,
                pr.motivo                                   AS argumento
            FROM predicciones_activas pa
            JOIN prospectos_rankeados pr
                ON pa.rut_normalizado = pr.rut_normalizado
            WHERE pa.probabilidad_adjudicacion > 60
            ORDER BY pa.probabilidad_adjudicacion DESC,
                     pr.monto_total_oc DESC
        """, conn)

        if not df.empty:
            df["P_win_pct"] = df["P_win_raw"].apply(
                lambda x: f"{x:.0f}%" if pd.notna(x) else "-"
            )
            df["fuente"] = "CSV — lag ~13 días"
            df.drop(columns=["P_win_raw"], inplace=True)
            return df
    except Exception as exc:
        print(f"  predecir_ganadores fallback: {exc}")

    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# EXCEL 3 HOJAS
# ─────────────────────────────────────────────────────────────

def _escribir_hoja(ws, wb, df: pd.DataFrame,
                   titulo: str, color_hdr: str,
                   subtitulo: str = ""):
    """Escribe un DataFrame en una hoja de xlsxwriter con formato."""
    fmt_t    = wb.add_format({"bold": True, "font_size": 13,
                               "font_color": color_hdr})
    fmt_s    = wb.add_format({"italic": True, "font_color": "#888888",
                               "font_size": 9})
    fmt_hdr  = wb.add_format({
        "bold": True, "bg_color": color_hdr, "font_color": "white",
        "border": 1, "align": "center", "text_wrap": True,
        "valign": "vcenter",
    })
    fmt_hoy  = wb.add_format({
        "bg_color": "#FFCDD2", "border": 1,
        "bold": True, "font_color": "#B71C1C",
    })
    fmt_sem  = wb.add_format({
        "bg_color": "#FFF9C4", "border": 1,
        "bold": True, "font_color": "#795548",
    })
    fmt_par  = wb.add_format({"bg_color": "#F5F5F5", "border": 1})
    fmt_norm = wb.add_format({"border": 1})

    ws.write(0, 0, titulo, fmt_t)
    ws.write(1, 0,
             subtitulo or f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
             fmt_s)

    if df.empty:
        ws.write(2, 0, "Sin alertas en este ciclo.", fmt_norm)
        return

    cols = list(df.columns)
    for ci, col in enumerate(cols):
        ws.write(2, ci, col, fmt_hdr)
    ws.set_row(2, 28)

    accion_idx = cols.index("accion_recomendada") if "accion_recomendada" in cols else None

    for ri in range(len(df)):
        accion = str(df.iloc[ri, accion_idx]) if accion_idx is not None else ""

        if accion == "LLAMAR HOY":
            fmt = fmt_hoy
        elif accion == "LLAMAR ESTA SEMANA":
            fmt = fmt_sem
        elif ri % 2 == 0:
            fmt = fmt_par
        else:
            fmt = fmt_norm

        for ci in range(len(cols)):
            val = df.iloc[ri, ci]
            val = "-" if pd.isna(val) or val is None else val
            ws.write(ri + 3, ci, val, fmt)
        ws.set_row(ri + 3, 20)

    for ci, col in enumerate(cols):
        try:
            max_len = max(
                len(str(col)),
                df.iloc[:, ci].astype(str).str.len().max()
            )
        except Exception:
            max_len = len(str(col))
        ws.set_column(ci, ci, min(max_len + 2, 50))

    ws.freeze_panes(3, 0)


def guardar_excel_3hojas(alertas_oc: list,
                         df_adj: pd.DataFrame,
                         df_pred: pd.DataFrame):
    """Escribe el Excel de alertas con 3 hojas (sobreescribe cada ciclo)."""
    ALERTAS_LOG.parent.mkdir(parents=True, exist_ok=True)

    df_oc = pd.DataFrame(alertas_oc) if alertas_oc else pd.DataFrame()
    if not df_oc.empty:
        df_oc = df_oc.sort_values("monto_MM", ascending=False).reset_index(drop=True)

    with pd.ExcelWriter(ALERTAS_LOG, engine="xlsxwriter") as writer:
        wb = writer.book

        _escribir_hoja(
            wb.add_worksheet("Alertas OC"),
            wb, df_oc,
            titulo=f"ALERTAS OC — {datetime.now().strftime('%d/%m/%Y')}",
            color_hdr="#1B5E20",
            subtitulo=f"{len(df_oc)} OC de Los Lagos detectadas hoy",
        )

        _escribir_hoja(
            wb.add_worksheet("Alertas Adjudicacion"),
            wb, df_adj,
            titulo="ADJUDICADAS SIN OC — Ventana de contacto",
            color_hdr="#0D47A1",
            subtitulo=(
                f"{len(df_adj)} empresas adjudicadas sin OC emitida aún "
                "— Contactar ANTES de que llegue el pago"
            ),
        )

        _fuente_p = (
            df_pred["fuente"].iloc[0]
            if not df_pred.empty and "fuente" in df_pred.columns
            else "sin datos"
        )
        _umbral_p = "40%" if "API" in _fuente_p else "60%"
        _escribir_hoja(
            wb.add_worksheet("Predecir Ganadores"),
            wb, df_pred,
            titulo="PREDECIR GANADORES — Contactar ANTES del cierre",
            color_hdr="#4A148C",
            subtitulo=(
                f"{len(df_pred)} empresas con P(ganar) >= {_umbral_p} "
                f"| Fuente: {_fuente_p} — Máxima ventana de oportunidad"
            ),
        )

    print(f"\n  Excel guardado: {ALERTAS_LOG}")
    print(f"  Hoja 1 — Alertas OC:          {len(df_oc):,} registros")
    print(f"  Hoja 2 — Alertas Adjudicación: {len(df_adj):,} registros")
    print(f"  Hoja 3 — Predecir Ganadores:   {len(df_pred):,} registros")


# ─────────────────────────────────────────────────────────────
# CONSOLA
# ─────────────────────────────────────────────────────────────

def mostrar_resumen(alertas_oc: list,
                    df_adj: pd.DataFrame,
                    df_pred: pd.DataFrame):
    """Resumen de las 3 hojas en consola."""
    urgentes_oc = [a for a in alertas_oc if a.get("accion_recomendada") == "LLAMAR HOY"]

    if urgentes_oc:
        print("\n" + "=" * 55)
        print("ALERTAS OC — LLAMAR HOY")
        print("=" * 55)
        for a in urgentes_oc:
            print(f"\n  ★ {a.get('empresa') or a.get('rut_proveedor')}")
            print(f"    Ciudad:    {a.get('ciudad')}")
            print(f"    Monto OC:  ${a.get('monto_MM')}M")
            print(f"    Organismo: {a.get('organismo')}")
            print(f"    Argumento: {a.get('argumento_llamada')}")

    if not df_adj.empty:
        n_hoy = (df_adj["accion_recomendada"] == "LLAMAR HOY").sum() \
                if "accion_recomendada" in df_adj.columns else 0
        print(f"\n  Adjudicadas sin OC: {len(df_adj)}"
              + (f"  ({n_hoy} requieren LLAMAR HOY)" if n_hoy else ""))
        for _, row in df_adj.head(3).iterrows():
            print(f"    >> {row.get('empresa', '-') or row.get('rut_proveedor','-')} | "
                  f"${row.get('monto_licitacion_MM', 0):.1f}M | "
                  f"adj hace {row.get('dias_desde_adjudicacion', '-')}d | "
                  f"{row.get('accion_recomendada', '-')}")

    if not df_pred.empty:
        _fuente_r = (
            df_pred["fuente"].iloc[0]
            if "fuente" in df_pred.columns else "?"
        )
        print(f"\n  Predecir Ganadores: {len(df_pred)} empresas ({_fuente_r})")
        for _, row in df_pred.head(3).iterrows():
            monto = row.get("monto_hist_oc_MM") or 0
            licit = row.get("licitacion_activa", "")
            extra = f" | {str(licit)[:40]}" if licit else ""
            print(f"    >> {row.get('empresa', '-')} | "
                  f"P={row.get('P_win_pct', '-')} | "
                  f"${float(monto):.1f}M{extra}")

    if not urgentes_oc and df_adj.empty and df_pred.empty:
        print("  Sin alertas prioritarias esta ronda.")


# ─────────────────────────────────────────────────────────────
# TIEMPO
# ─────────────────────────────────────────────────────────────

def get_ultimo_check() -> datetime:
    if ULTIMO_CHECK.exists():
        try:
            return datetime.fromisoformat(ULTIMO_CHECK.read_text().strip())
        except Exception:
            pass
    return datetime.now() - timedelta(hours=4)


def guardar_check():
    ULTIMO_CHECK.write_text(datetime.now().isoformat())


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    ahora = datetime.now()
    print("=" * 55)
    print(f"alertas_tiempo_real.py v3 — {ahora.strftime('%d/%m/%Y %H:%M')}")
    print("OC emitidas | Adjudicadas sin OC | Predecir ganadores")
    print("=" * 55)

    if not (8 <= ahora.hour < 19):
        print("Fuera de horario hábil (8:00-19:00).")
        return

    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        print("ERROR: falta xlsxwriter — pip install xlsxwriter")
        return

    conn = sqlite3.connect(DB_PATH)

    # ── FLUJO 1: OC (DB-first, API como bonus) ───────────────
    print("\n[1/3] Alertas OC — DB local + API si disponible...")
    codigos_lagos = get_organismos_lagos(conn)

    alertas_oc: list = []
    alertas_oc = procesar_oc_dia(conn, codigos_lagos)
    print(f"  OC con alerta: {len(alertas_oc)}")

    # ── FLUJO 2: Adjudicadas sin OC ───────────────────────────
    print("\n[2/3] Alertas Adjudicación — ventana adj→OC...")
    df_adj_raw = get_adjudicadas_sin_oc(conn)
    print(f"  Adjudicadas sin OC: {len(df_adj_raw)}")

    df_adj = pd.DataFrame()
    if not df_adj_raw.empty:
        df_adj = enriquecer_adjudicadas(df_adj_raw, conn)

    # ── FLUJO 3: Predecir ganadores ───────────────────────────
    print("\n[3/3] Predecir Ganadores — API (>=40%) o CSV fallback (>60%)...")
    df_pred = get_predecir_ganadores(conn)
    _fuente_log = (
        df_pred["fuente"].iloc[0]
        if not df_pred.empty and "fuente" in df_pred.columns
        else "sin datos"
    )
    print(f"  Empresas detectadas: {len(df_pred)} ({_fuente_log})")

    # ── Mostrar y guardar ─────────────────────────────────────
    mostrar_resumen(alertas_oc, df_adj, df_pred)
    guardar_excel_3hojas(alertas_oc, df_adj, df_pred)
    guardar_check()

    conn.close()
    print("\nPróxima revisión en 30 minutos.")


if __name__ == "__main__":
    run()
