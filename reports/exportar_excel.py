# =============================================================
#  reports/exportar_excel.py
#
#  Genera el Excel final con 4 hojas:
#    1. Nivel 1 — Contactar hoy
#    2. Nivel 2 — Contactar esta semana
#    3. Nivel 3 — Con historial OC
#    4. Resumen ejecutivo
#
#  Output: output/prospectos_factoring.xlsx
# =============================================================

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, EXCEL_OUTPUT, PESOS_SCORING, PRED_WIN_THRESHOLD
from utils.helpers import mapa_tramo


def formatear_monto(valor) -> str:
    try:
        v = float(valor)
        if v == 0: return "-"
        if v >= 1_000_000_000: return f"${v/1_000_000_000:.1f}B"
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        return f"${v:,.0f}"
    except Exception:
        return "-"


def preparar_hoja(df: pd.DataFrame) -> pd.DataFrame:
    COLS_EXCEL = {
        "ranking":                   "Ranking",
        "urgencia_contacto":         "Urgencia",
        "ventana_estrategia":        "Estrategia",
        "estado_crm":                "Estado CRM",
        "score":                     "Score",
        "razon_social":              "Empresa",
        "comuna":                    "Ciudad",
        "actividad_economica":       "Actividad (2024)",
        "actividad_2026":            "Actividad (2026)",
        "tramo_ventas":              "Tramo ventas",
        "num_trabajadores":          "Trabajadores",
        "vigente_2026":              "Vigente 2026",
        "licitaciones_ganadas":      "Licit. ganadas",
        "total_oc":                  "N OC",
        "monto_total_oc":            "Monto OC total",
        "monto_prom_oc":             "Monto prom OC",
        "ultima_oc":                 "Ultima OC",
        "organismos_distintos":      "Organismos",
        "probabilidad_adjudicacion": "P(ganar licit.)",
        "rut_normalizado":           "RUT",
        "crm_ejecutivo":             "Ejecutivo CRM",
        "crm_ultimo_contacto":       "Ultimo contacto",
        "crm_proxima_accion":        "Proxima accion",
        "crm_notas":                 "Notas CRM",
        "motivo":                    "Argumento de contacto",
        "motivo_urgencia":           "Por que ahora",
    }
    cols_map = {k: v for k, v in COLS_EXCEL.items()
                if k in df.columns}
    df_out = df[list(cols_map.keys())].copy()
    df_out = df_out.rename(columns=cols_map)

    for col in ["Monto OC", "Monto prom OC"]:
        if col in df_out.columns:
            df_out[col] = df_out[col].apply(formatear_monto)

    if "Vigente 2026" in df_out.columns:
        df_out["Vigente 2026"] = df_out["Vigente 2026"].map(
            {1: "Si", 0: "No"}
        ).fillna("Si")

    if "Tramo ventas" in df_out.columns:
        df_out["Tramo ventas"] = (
            df_out["Tramo ventas"].astype(str).str.strip()
            .map(mapa_tramo).fillna(df_out["Tramo ventas"])
        )

    if "P(ganar licit.)" in df_out.columns:
        df_out["P(ganar licit.)"] = df_out["P(ganar licit.)"].apply(
            lambda x: f"{float(x):.0f}%"
            if pd.notna(x) and str(x) not in ("", "-", "nan") else "-"
        )

    if "Ultima OC" in df_out.columns:
        df_out["Ultima OC"] = pd.to_datetime(
            df_out["Ultima OC"], errors="coerce"
        ).dt.strftime("%d-%m-%Y").fillna("-")

    for col in ["Licit. ganadas", "N OC", "Organismos"]:
        if col in df_out.columns:
            df_out[col] = pd.to_numeric(
                df_out[col], errors="coerce"
            ).fillna(0).astype(int)

    return df_out


def escribir_hoja(writer, df_hoja, nombre, color_bg, descripcion):
    if df_hoja.empty:
        return
    df_fmt = preparar_hoja(df_hoja)
    wb = writer.book

    fmt_hdr = wb.add_format({
        "bold": True, "bg_color": color_bg,
        "font_color": "white", "border": 1,
        "align": "center", "valign": "vcenter",
    })
    fmt_titulo = wb.add_format({
        "bold": True, "font_size": 13,
        "font_color": color_bg,
    })
    fmt_subtitulo = wb.add_format({
        "italic": True, "font_color": "#888888",
    })
    fmt_par  = wb.add_format({"bg_color": "#F5F5F5", "border": 1})
    fmt_norm = wb.add_format({"border": 1})

    df_fmt.to_excel(writer, sheet_name=nombre,
                    startrow=3, index=False)
    ws = writer.sheets[nombre]

    ws.write(0, 0, descripcion, fmt_titulo)
    ws.write(1, 0,
             f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} "
             f"| Los Lagos | {len(df_hoja)} empresas",
             fmt_subtitulo)

    for ci, cn in enumerate(df_fmt.columns):
        ws.write(3, ci, cn, fmt_hdr)

    for ri in range(len(df_fmt)):
        fmt = fmt_par if ri % 2 == 0 else fmt_norm
        for ci in range(len(df_fmt.columns)):
            val = df_fmt.iloc[ri, ci]
            try:
                if pd.isna(val):
                    val = "-"
            except (TypeError, ValueError):
                pass
            ws.write(ri + 4, ci, val, fmt)

    for ci, cn in enumerate(df_fmt.columns):
        max_w = max(
            len(str(cn)),
            df_fmt[cn].astype(str).str.len().max()
            if len(df_fmt) > 0 else 10
        )
        ws.set_column(ci, ci, min(max_w + 3, 55))

    ws.freeze_panes(4, 0)


def escribir_hoja_plazos(writer, conn: sqlite3.Connection):
    """Hoja 5 — Plazos de pago por organismo (BI para el equipo comercial)."""
    try:
        df = pd.read_sql("""
            SELECT
                nombre_organismo,
                region_organismo,
                n_oc,
                ROUND(monto_total_clp / 1000000.0, 1) AS monto_total_MM,
                dias_promedio,
                dias_mediana,
                score_velocidad_pago,
                categoria
            FROM plazos_pago_organismos
            ORDER BY score_velocidad_pago DESC
        """, conn)
    except Exception:
        return   # tabla no existe aún — silencioso

    if df.empty:
        return

    wb = writer.book
    color = "#37474F"
    fmt_hdr = wb.add_format({
        "bold": True, "bg_color": color,
        "font_color": "white", "border": 1,
        "align": "center",
    })
    fmt_titulo = wb.add_format({"bold": True, "font_size": 13,
                                "font_color": color})
    fmt_subtit = wb.add_format({"italic": True, "font_color": "#888888"})
    fmt_rap  = wb.add_format({"bg_color": "#C8E6C9", "border": 1})
    fmt_norm = wb.add_format({"bg_color": "#FFF9C4", "border": 1})
    fmt_lent = wb.add_format({"bg_color": "#FFCDD2", "border": 1})
    fmt_def  = wb.add_format({"border": 1})

    nombres_col = {
        "nombre_organismo":   "Organismo",
        "region_organismo":   "Región",
        "n_oc":               "N° OC",
        "monto_total_MM":     "Monto total ($MM)",
        "dias_promedio":      "Días prom",
        "dias_mediana":       "Días mediana",
        "score_velocidad_pago": "Score velocidad",
        "categoria":          "Categoría",
    }

    df_out = df.rename(columns=nombres_col)
    df_out.to_excel(writer, sheet_name="Plazos de Pago", startrow=3, index=False)
    ws = writer.sheets["Plazos de Pago"]

    ws.write(0, 0, "VELOCIDAD DE PAGO POR ORGANISMO — Patagonia Factoring", fmt_titulo)
    ws.write(1, 0,
             f"RAPIDO <= 20 días | NORMAL 21-50 días | LENTO > 50 días  "
             f"| {len(df)} organismos | Generado {datetime.now().strftime('%d/%m/%Y')}",
             fmt_subtit)

    for ci, cn in enumerate(df_out.columns):
        ws.write(3, ci, cn, fmt_hdr)

    color_map = {"RAPIDO": fmt_rap, "NORMAL": fmt_norm,
                 "LENTO": fmt_lent, "SIN DATO": fmt_def}

    for ri in range(len(df_out)):
        cat = str(df.iloc[ri]["categoria"])
        fmt = color_map.get(cat, fmt_def)
        for ci in range(len(df_out.columns)):
            val = df_out.iloc[ri, ci]
            try:
                if pd.isna(val):
                    val = "-"
            except (TypeError, ValueError):
                pass
            ws.write(ri + 4, ci, val, fmt)

    for ci, cn in enumerate(df_out.columns):
        max_w = max(len(str(cn)),
                    df_out[cn].astype(str).str.len().max()
                    if len(df_out) > 0 else 10)
        ws.set_column(ci, ci, min(max_w + 3, 55))

    ws.freeze_panes(4, 0)
    print(f"  Hoja Plazos de Pago: {len(df)} organismos")


def escribir_hoja_preadjudicacion(writer, conn: sqlite3.Connection):
    """Hoja 6 — Pre-adjudicación: licitaciones activas con alta P(win)."""
    df_pred = pd.DataFrame()

    # Intentar desde predicciones_licitaciones_lagos (más detallada)
    try:
        df_pred = pd.read_sql(f"""
            SELECT
                p.rut_normalizado,
                p.id_licitacion,
                ROUND(p.probabilidad_win * 100, 1) AS prob_win_pct,
                pr.razon_social,
                pr.comuna,
                pr.tramo_ventas,
                pr.score           AS score_prospecto,
                pr.nivel,
                pr.urgencia_contacto,
                l.nombre           AS licitacion,
                l.monto_estimado,
                l.fechacierre,
                l.organismo
            FROM predicciones_licitaciones_lagos p
            LEFT JOIN prospectos_rankeados pr
                ON p.rut_normalizado = pr.rut_normalizado
            LEFT JOIN licitaciones_activas_lagos l
                ON p.id_licitacion = l.codigo
            WHERE p.probabilidad_win >= {PRED_WIN_THRESHOLD / 100.0}
              AND pr.nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
            ORDER BY p.probabilidad_win DESC
            LIMIT 100
        """, conn)
    except Exception:
        pass

    # Fallback: predicciones_activas (solo por empresa, sin detalle licitación)
    if df_pred.empty:
        try:
            df_pred = pd.read_sql(f"""
                SELECT
                    pa.rut_normalizado,
                    pa.probabilidad_adjudicacion AS prob_win_pct,
                    pr.razon_social,
                    pr.comuna,
                    pr.tramo_ventas,
                    pr.score           AS score_prospecto,
                    pr.nivel,
                    pr.urgencia_contacto,
                    pr.motivo_urgencia
                FROM predicciones_activas pa
                JOIN prospectos_rankeados pr
                    ON pa.rut_normalizado = pr.rut_normalizado
                WHERE pa.probabilidad_adjudicacion >= {PRED_WIN_THRESHOLD}
                  AND pr.nivel IN ('1 - Contactar hoy',
                                   '2 - Contactar esta semana')
                ORDER BY pa.probabilidad_adjudicacion DESC
                LIMIT 100
            """, conn)
        except Exception:
            return

    if df_pred.empty:
        return

    wb = writer.book
    color = "#1565C0"
    fmt_hdr = wb.add_format({
        "bold": True, "bg_color": color,
        "font_color": "white", "border": 1, "align": "center",
    })
    fmt_titulo = wb.add_format({"bold": True, "font_size": 13,
                                "font_color": color})
    fmt_subtit = wb.add_format({"italic": True, "font_color": "#888888"})
    fmt_par  = wb.add_format({"bg_color": "#E3F2FD", "border": 1})
    fmt_norm = wb.add_format({"border": 1})

    # Formatear columnas clave
    if "monto_estimado" in df_pred.columns:
        df_pred["monto_estimado"] = df_pred["monto_estimado"].apply(
            formatear_monto
        )
    if "tramo_ventas" in df_pred.columns:
        df_pred["tramo_ventas"] = (
            df_pred["tramo_ventas"].astype(str).str.strip()
            .map(mapa_tramo).fillna(df_pred["tramo_ventas"])
        )

    df_pred.to_excel(writer, sheet_name="Pre-Adjudicacion",
                     startrow=3, index=False)
    ws = writer.sheets["Pre-Adjudicacion"]

    ws.write(0, 0,
             f"ESTRATEGIA 1 — CONTACTAR ANTES DEL CIERRE (P(win) >= {PRED_WIN_THRESHOLD}%)",
             fmt_titulo)
    ws.write(1, 0,
             f"Empresas de Los Lagos con alta probabilidad de ganar licitaciones "
             f"activas | {len(df_pred)} prospectos | "
             f"Generado {datetime.now().strftime('%d/%m/%Y')}",
             fmt_subtit)

    for ci, cn in enumerate(df_pred.columns):
        ws.write(3, ci, cn, fmt_hdr)

    for ri in range(len(df_pred)):
        fmt = fmt_par if ri % 2 == 0 else fmt_norm
        for ci in range(len(df_pred.columns)):
            val = df_pred.iloc[ri, ci]
            try:
                if pd.isna(val):
                    val = "-"
            except (TypeError, ValueError):
                pass
            ws.write(ri + 4, ci, val, fmt)

    for ci, cn in enumerate(df_pred.columns):
        max_w = max(len(str(cn)),
                    df_pred[cn].astype(str).str.len().max()
                    if len(df_pred) > 0 else 10)
        ws.set_column(ci, ci, min(max_w + 3, 55))

    ws.freeze_panes(4, 0)
    print(f"  Hoja Pre-Adjudicación: {len(df_pred)} prospectos")


def escribir_hoja_crm(writer, conn: sqlite3.Connection):
    """
    Hoja CRM — métricas de conversión y pipeline comercial.
    Muestra el embudo: PENDIENTE → CONTACTADO → INTERESADO → PROPUESTA → CLIENTE.

    ¿Con qué datos?
    Requiere tabla crm_contactos. Si no existe (nunca se corrió
    ingesta/crm_contactos.py --init), la hoja se omite silenciosamente.
    """
    try:
        df_crm = pd.read_sql("""
            SELECT
                c.rut_normalizado,
                COALESCE(c.razon_social, pr.razon_social)     AS empresa,
                COALESCE(pr.comuna, '-')                      AS ciudad,
                c.estado_crm,
                c.ejecutivo,
                c.fecha_primer_contacto,
                c.fecha_ultimo_contacto,
                c.fecha_proxima_accion,
                c.notas,
                COALESCE(pr.score, 0)                         AS score,
                COALESCE(pr.nivel, '-')                       AS nivel,
                COALESCE(pr.urgencia_contacto, '-')           AS urgencia,
                COALESCE(pr.monto_prom_oc, 0)                 AS monto_prom_oc,
                COALESCE(pr.total_oc, 0)                      AS total_oc,
                COALESCE(pr.tramo_ventas, '-')                AS tramo_ventas
            FROM crm_contactos c
            LEFT JOIN prospectos_rankeados pr
                ON c.rut_normalizado = pr.rut_normalizado
            ORDER BY
                CASE c.estado_crm
                    WHEN 'INTERESADO'       THEN 1
                    WHEN 'PROPUESTA_ENVIADA' THEN 2
                    WHEN 'CONTACTADO'       THEN 3
                    WHEN 'PENDIENTE'        THEN 4
                    WHEN 'CLIENTE'          THEN 5
                    WHEN 'RECHAZADO'        THEN 6
                    WHEN 'NO_APLICA'        THEN 7
                    ELSE 8
                END, pr.score DESC
        """, conn)
    except Exception:
        return   # tabla no existe aún

    if df_crm.empty:
        return

    wb = writer.book
    color = "#E65100"

    # Métricas de embudo
    total       = len(df_crm)
    n_pend      = (df_crm["estado_crm"] == "PENDIENTE").sum()
    n_cont      = (df_crm["estado_crm"] == "CONTACTADO").sum()
    n_inter     = (df_crm["estado_crm"] == "INTERESADO").sum()
    n_prop      = (df_crm["estado_crm"] == "PROPUESTA_ENVIADA").sum()
    n_cliente   = (df_crm["estado_crm"] == "CLIENTE").sum()
    n_rechazado = (df_crm["estado_crm"] == "RECHAZADO").sum()

    # Hoja resumen funnel primero
    ws_funnel = wb.add_worksheet("CRM - Embudo")
    fmt_t  = wb.add_format({"bold": True, "font_size": 14, "font_color": color})
    fmt_s  = wb.add_format({"italic": True, "font_color": "#888888"})
    fmt_hdr = wb.add_format({"bold": True, "bg_color": color,
                             "font_color": "white", "border": 1})
    fmt_v   = wb.add_format({"align": "right", "bold": True, "border": 1})
    fmt_lbl = wb.add_format({"border": 1})
    fmt_pct = wb.add_format({"align": "right", "font_color": "#1565C0", "border": 1})

    ws_funnel.write(0, 0, "EMBUDO COMERCIAL — Patagonia Factoring SpA", fmt_t)
    ws_funnel.write(1, 0,
                    f"Total prospectos: {total} | "
                    f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                    fmt_s)

    filas_funnel = [
        ("Etapa", "N°", "% del total"),
        ("PENDIENTE (no contactados)",   n_pend,      f"{n_pend/max(total,1)*100:.1f}%"),
        ("CONTACTADO",                   n_cont,      f"{n_cont/max(total,1)*100:.1f}%"),
        ("INTERESADO",                   n_inter,     f"{n_inter/max(total,1)*100:.1f}%"),
        ("PROPUESTA ENVIADA",            n_prop,      f"{n_prop/max(total,1)*100:.1f}%"),
        ("CLIENTE (convertido)",         n_cliente,   f"{n_cliente/max(total,1)*100:.1f}%"),
        ("RECHAZADO",                    n_rechazado, f"{n_rechazado/max(total,1)*100:.1f}%"),
        ("", "", ""),
        ("Tasa de conversión (Clientes/Contactados)",
         f"{n_cliente/max(n_cont+n_inter+n_prop+n_cliente,1)*100:.1f}%", ""),
    ]

    for i, (lbl, val, pct) in enumerate(filas_funnel):
        row_idx = i + 3
        if lbl == "Etapa":
            ws_funnel.write(row_idx, 0, lbl, fmt_hdr)
            ws_funnel.write(row_idx, 1, val, fmt_hdr)
            ws_funnel.write(row_idx, 2, pct, fmt_hdr)
        else:
            ws_funnel.write(row_idx, 0, lbl, fmt_lbl)
            ws_funnel.write(row_idx, 1, val, fmt_v)
            ws_funnel.write(row_idx, 2, pct, fmt_pct)

    ws_funnel.set_column(0, 0, 45)
    ws_funnel.set_column(1, 1, 10)
    ws_funnel.set_column(2, 2, 15)

    # Hoja detalle CRM
    fmt_hdr2 = wb.add_format({"bold": True, "bg_color": color,
                              "font_color": "white", "border": 1, "align": "center"})
    fmt_inter = wb.add_format({"bg_color": "#FFF9C4", "border": 1})
    fmt_cli   = wb.add_format({"bg_color": "#C8E6C9", "border": 1})
    fmt_rec   = wb.add_format({"bg_color": "#FFCDD2", "border": 1})
    fmt_par   = wb.add_format({"bg_color": "#F5F5F5", "border": 1})
    fmt_norm2 = wb.add_format({"border": 1})

    nombres_col = {
        "rut_normalizado":      "RUT",
        "empresa":              "Empresa",
        "ciudad":               "Ciudad",
        "estado_crm":           "Estado CRM",
        "ejecutivo":            "Ejecutivo",
        "fecha_ultimo_contacto":"Ult. contacto",
        "fecha_proxima_accion": "Prox. accion",
        "score":                "Score",
        "nivel":                "Nivel",
        "monto_prom_oc":        "Monto prom OC",
        "notas":                "Notas",
    }
    df_out = df_crm[list(nombres_col.keys())].rename(columns=nombres_col).copy()
    df_out["Monto prom OC"] = df_out["Monto prom OC"].apply(formatear_monto)

    df_out.to_excel(writer, sheet_name="CRM - Detalle", startrow=3, index=False)
    ws_det = writer.sheets["CRM - Detalle"]
    ws_det.write(0, 0, "CRM DETALLE — Seguimiento de prospectos", fmt_t)
    ws_det.write(1, 0,
                 f"{len(df_out)} registros | "
                 f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                 fmt_s)

    for ci, cn in enumerate(df_out.columns):
        ws_det.write(3, ci, cn, fmt_hdr2)

    color_fila = {
        "INTERESADO": fmt_inter, "PROPUESTA_ENVIADA": fmt_inter,
        "CLIENTE": fmt_cli, "RECHAZADO": fmt_rec, "NO_APLICA": fmt_rec,
    }
    for ri in range(len(df_out)):
        estado = str(df_crm.iloc[ri]["estado_crm"])
        fmt = color_fila.get(estado, fmt_par if ri % 2 == 0 else fmt_norm2)
        for ci in range(len(df_out.columns)):
            val = df_out.iloc[ri, ci]
            try:
                val = "-" if pd.isna(val) else val
            except (TypeError, ValueError):
                pass
            ws_det.write(ri + 4, ci, val, fmt)

    for ci, cn in enumerate(df_out.columns):
        max_w = max(len(str(cn)),
                    df_out[cn].astype(str).str.len().max()
                    if len(df_out) > 0 else 10)
        ws_det.set_column(ci, ci, min(max_w + 3, 50))

    ws_det.freeze_panes(4, 0)
    print(f"  Hoja CRM: {total} registros | {n_cliente} clientes | "
          f"{n_inter} interesados | {n_pend} pendientes")


def run():
    print("=" * 55)
    print("reports/exportar_excel.py")
    print("Generando Excel final de prospectos")
    print("=" * 55)

    # Verificar que xlsxwriter está instalado
    try:
        import xlsxwriter
    except ImportError:
        print("\nERROR: falta xlsxwriter")
        print("Instalar con: pip install xlsxwriter")
        return

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM prospectos_rankeados", conn)

    # Enriquecer con predicciones de adjudicación si existen
    try:
        df_pred = pd.read_sql(
            "SELECT rut_normalizado, probabilidad_adjudicacion "
            "FROM predicciones_activas",
            conn
        )
        df = df.merge(df_pred, on="rut_normalizado", how="left")
        print(f"  Predicciones cargadas: {df_pred['rut_normalizado'].nunique():,} empresas")
    except Exception:
        df["probabilidad_adjudicacion"] = None
    # conn permanece abierto — se cierra al final del run()

    print(f"\nTotal prospectos: {len(df):,}")

    # Ordenar por urgencia antes de filtrar por nivel
    _urgencia_ord = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    if "urgencia_contacto" in df.columns:
        df["_urgencia_sort"] = (
            df["urgencia_contacto"].map(_urgencia_ord).fillna(2)
        )
        df = df.sort_values(
            ["_urgencia_sort", "score", "monto_total_oc"],
            ascending=[True, False, False]
        ).drop(columns=["_urgencia_sort"])

    df_n1 = df[df["nivel"] == "1 - Contactar hoy"].copy()
    df_n2 = df[df["nivel"] == "2 - Contactar esta semana"].copy()
    df_n3 = df[
        df["nivel"].isin(["3 - Solo SII", "3 - Empresa cerrada"]) &
        (df["aparece_en_oc"] == 1)
    ].copy()

    EXCEL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Distribución por ventana de estrategia (para resumen)
    dist_ventana = {}
    if "ventana_estrategia" in df.columns:
        dist_ventana = df[
            df["nivel"].isin(["1 - Contactar hoy", "2 - Contactar esta semana"])
        ]["ventana_estrategia"].value_counts().to_dict()

    with pd.ExcelWriter(EXCEL_OUTPUT, engine="xlsxwriter") as writer:

        escribir_hoja(writer, df_n1,
            "NIVEL 1 - Hoy", "#1B5E20",
            "NIVEL 1 — Contactar HOY — Patagonia Factoring")

        escribir_hoja(writer, df_n2,
            "NIVEL 2 - Esta semana", "#0D47A1",
            "NIVEL 2 — Contactar esta semana — Patagonia Factoring")

        escribir_hoja(writer, df_n3,
            "NIVEL 3 - Con OC", "#4A148C",
            "NIVEL 3 — Con historial OC, score bajo")

        # Hojas 5, 6, 7 — nuevas
        escribir_hoja_plazos(writer, conn)
        escribir_hoja_preadjudicacion(writer, conn)
        escribir_hoja_crm(writer, conn)

        # Hoja Resumen — dinámica desde PESOS_SCORING
        wb = writer.book
        ws = wb.add_worksheet("Resumen")
        fmt_t = wb.add_format({"bold": True, "font_size": 15,
                               "font_color": "#1B5E20"})
        fmt_s = wb.add_format({"bold": True, "bg_color": "#E8F5E9",
                               "font_color": "#1B5E20"})
        fmt_v = wb.add_format({"align": "right", "bold": True})

        n_alta  = int((df["urgencia_contacto"] == "ALTA").sum())  \
                  if "urgencia_contacto" in df.columns else "-"
        n_media = int((df["urgencia_contacto"] == "MEDIA").sum()) \
                  if "urgencia_contacto" in df.columns else "-"

        filas = [
            ("PROSPECTOS FACTORING — REGION DE LOS LAGOS", ""),
            ("Patagonia Factoring SpA", ""),
            (f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}", ""),
            ("", ""),
            ("UNIVERSO ANALIZADO", ""),
            ("Empresas Los Lagos vigentes feb 2026",
             f"{int(df['vigente_2026'].sum()):,}"
             if "vigente_2026" in df.columns else "-"),
            ("Con historial Mercado Publico",
             f"{int(df['aparece_en_mp'].sum()):,}"),
            ("Con ordenes de compra",
             f"{int(df['aparece_en_oc'].sum()):,}"),
            ("", ""),
            ("RESULTADOS DE PROSPECCIÓN", ""),
            ("Nivel 1 — Contactar HOY",      f"{len(df_n1):,}"),
            ("Nivel 2 — Esta semana",         f"{len(df_n2):,}"),
            ("Nivel 3 — Con OC (bajo score)", f"{len(df_n3):,}"),
            ("Urgencia ALTA",                 f"{n_alta:,}"),
            ("Urgencia MEDIA",                f"{n_media:,}"),
            ("", ""),
            ("DISTRIBUCIÓN POR ESTRATEGIA (Niv 1+2)", ""),
        ]

        etiquetas_ventana = {
            "E1 — Pre-adjudicación": "Estrategia 1 — Contactar antes cierre",
            "E2 — Adj a OC":         "Estrategia 2 — Adj→OC (ventana clave)",
            "E3 — OC emitida":       "Estrategia 3 — OC emitida esta semana",
            "—":                     "Sin ventana activa",
        }
        for k, label in etiquetas_ventana.items():
            cnt = dist_ventana.get(k, 0)
            filas.append((f"  {label}", str(cnt)))

        filas += [("", ""), ("TOP CIUDADES (Nivel 1+2)", "")]
        top = (
            df[df["nivel"].isin([
                "1 - Contactar hoy", "2 - Contactar esta semana"
            ])]["comuna"].value_counts().head(8)
        )
        for ciudad, cnt in top.items():
            filas.append((f"  {ciudad}", str(cnt)))

        filas += [("", ""), ("PESOS DEL SCORE (v2)", "")]
        nombres_peso = {
            "f_historial":                  "Historial adjudicaciones",
            "f_tramo_ventas":               "Tramo ventas SII",
            "f_capital_negativo":           "Capital negativo (tension)",
            "f_monto_oc":                   "Monto promedio OC",
            "f_oc_reciente":                "OC reciente (<12 meses)",
            "f_dias_entre_adj_oc":          "Velocidad adj→OC empresa",
            "f_licitacion_grande_reciente": "Licitacion grande reciente",
            "f_tasa_adjudicacion":          "Tasa de adjudicacion OCDS",
            "f_crecimiento_oc_yoy":         "Crecimiento OC anual",
            "f_plazo_pago_cliente":         "Velocidad pago organismos",
            "f_antiguedad":                 "Antiguedad empresa",
            "f_rubro_prioritario":          "Rubro prioritario",
        }
        for feat, peso in PESOS_SCORING.items():
            label = nombres_peso.get(feat, feat)
            filas.append((f"  {label}", f"{int(peso*100)}%"))

        secciones = {
            "UNIVERSO ANALIZADO", "RESULTADOS DE PROSPECCIÓN",
            "DISTRIBUCIÓN POR ESTRATEGIA (Niv 1+2)",
            "TOP CIUDADES (Nivel 1+2)", "PESOS DEL SCORE (v2)",
        }

        for i, (et, val) in enumerate(filas):
            if i == 0:
                ws.write(i, 0, et, fmt_t)
            elif et in secciones:
                ws.write(i, 0, et, fmt_s)
                ws.write(i, 1, val, fmt_s)
            else:
                ws.write(i, 0, et)
                ws.write(i, 1, val, fmt_v)

        ws.set_column(0, 0, 48)
        ws.set_column(1, 1, 22)

    conn.close()
    print(f"\nExcel guardado en: {EXCEL_OUTPUT}")
    print(f"  Hoja 1 — Nivel 1:            {len(df_n1)} empresas")
    print(f"  Hoja 2 — Nivel 2:            {len(df_n2)} empresas")
    print(f"  Hoja 3 — Nivel 3:            {len(df_n3)} empresas")
    print(f"  Hoja 4 — Pre-Adjudicación:   ver log arriba")
    print(f"  Hoja 5 — Plazos de Pago:     ver log arriba")
    print(f"  Hoja 6 — CRM Embudo:         ver log arriba")
    print(f"  Hoja 7 — CRM Detalle:        ver log arriba")
    print(f"  Hoja 8 — Resumen ejecutivo")


if __name__ == "__main__":
    run()