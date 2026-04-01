# =============================================================
#  tests/top15_reunion.py
#  Genera Excel ejecutivo de las top 15 empresas
#  para presentacion en reunion con GG del factoring
# =============================================================

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.helpers import mapa_tramo

DB_PATH = Path("data/factoring_prospeccion.db")
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql("""
    SELECT
        razon_social                            AS empresa,
        rut_normalizado                         AS rut,
        comuna,
        region,
        actividad_economica                     AS actividad,
        actividad_2026                          AS actividad_actualizada_2026,
        tramo_ventas,
        num_trabajadores                        AS trabajadores,
        vigente_2026,
        licitaciones_ganadas,
        total_oc                                AS total_ordenes_compra,
        ROUND(monto_total_oc / 1000000, 1)      AS monto_total_oc_MM,
        ROUND(monto_prom_oc  / 1000000, 2)      AS monto_promedio_oc_MM,
        ultima_oc,
        organismos_distintos,
        tramo_capital_negativo                  AS capital_negativo,
        score,
        motivo                                  AS por_que_es_prospecto
    FROM prospectos_rankeados
    WHERE nivel = '1 - Contactar hoy'
    ORDER BY ultima_oc DESC, monto_total_oc DESC
    LIMIT 15
""", conn)
conn.close()

df = df.reset_index(drop=True)
df.insert(0, "prioridad", df.index + 1)

# Formatear tramo ventas
df["tramo_ventas"] = (
    df["tramo_ventas"].astype(str).str.strip()
    .map(mapa_tramo).fillna(df["tramo_ventas"])
)

# Formatear vigente
df["vigente_2026"] = df["vigente_2026"].map(
    {1:"Sí",0:"No"}
).fillna("Sí")

# Formatear fecha
df["ultima_oc"] = pd.to_datetime(
    df["ultima_oc"], errors="coerce"
).dt.strftime("%d-%m-%Y").fillna("-")

# Calcular oportunidad estimada (monto prom * 0.03 = comisión 3%)
df["oportunidad_mensual_MM"] = (
    df["monto_promedio_oc_MM"] * 0.03
).round(3)

output = Path("output/TOP15_presentacion_ejecutiva.xlsx")
output.parent.mkdir(exist_ok=True)

with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
    wb = writer.book

    # ── Formatos ──────────────────────────────────────────────
    fmt_titulo = wb.add_format({
        "bold":True,"font_size":18,
        "font_color":"#1B5E20","align":"left",
    })
    fmt_subtitulo = wb.add_format({
        "italic":True,"font_size":11,
        "font_color":"#666666",
    })
    fmt_hdr = wb.add_format({
        "bold":True,"bg_color":"#1B5E20",
        "font_color":"white","border":1,
        "align":"center","valign":"vcenter",
        "text_wrap":True,
    })
    fmt_hdr_naranja = wb.add_format({
        "bold":True,"bg_color":"#E65100",
        "font_color":"white","border":1,
        "align":"center","valign":"vcenter",
        "text_wrap":True,
    })
    fmt_par  = wb.add_format({
        "bg_color":"#F1F8E9","border":1,
        "valign":"vcenter",
    })
    fmt_norm = wb.add_format({
        "border":1,"valign":"vcenter",
    })
    fmt_monto = wb.add_format({
        "border":1,"valign":"vcenter",
        "num_format":"#,##0.0","bold":True,
        "font_color":"#1B5E20",
    })
    fmt_monto_par = wb.add_format({
        "bg_color":"#F1F8E9","border":1,
        "valign":"vcenter","num_format":"#,##0.0",
        "bold":True,"font_color":"#1B5E20",
    })
    fmt_score = wb.add_format({
        "border":1,"valign":"vcenter",
        "bold":True,"font_color":"#0D47A1",
        "align":"center",
    })
    fmt_score_par = wb.add_format({
        "bg_color":"#F1F8E9","border":1,
        "valign":"vcenter","bold":True,
        "font_color":"#0D47A1","align":"center",
    })
    fmt_prio = wb.add_format({
        "bold":True,"bg_color":"#1B5E20",
        "font_color":"white","border":1,
        "align":"center","valign":"vcenter",
        "font_size":12,
    })
    fmt_prio_par = wb.add_format({
        "bold":True,"bg_color":"#2E7D32",
        "font_color":"white","border":1,
        "align":"center","valign":"vcenter",
        "font_size":12,
    })

    # ══════════════════════════════════════════════════════════
    # HOJA 1: TOP 15 PROSPECTOS
    # ══════════════════════════════════════════════════════════
    ws = wb.add_worksheet("TOP 15 Prospectos")

    ws.write(0, 0,
        "TOP 15 PROSPECTOS — FACTORING REGIÓN DE LOS LAGOS",
        fmt_titulo)
    ws.write(1, 0,
        f"Generado: {datetime.now().strftime('%d/%m/%Y')} "
        f"| Fuente: Mercado Público + SII | "
        f"Análisis: Data Science aplicado a prospección comercial",
        fmt_subtitulo)
    ws.write(2, 0, "", wb.add_format())

    # Definir columnas y sus anchos
    columnas = [
        ("prioridad",                  "#",              6),
        ("empresa",                    "Empresa",        42),
        ("comuna",                     "Ciudad",         14),
        ("tramo_ventas",               "Tamaño",         10),
        ("trabajadores",               "Trabajadores",    9),
        ("vigente_2026",               "Vigente 2026",    9),
        ("licitaciones_ganadas",       "Licit. ganadas",  9),
        ("total_ordenes_compra",       "N° OC",           7),
        ("monto_total_oc_MM",          "Monto OC (MM$)", 12),
        ("monto_promedio_oc_MM",       "Prom OC (MM$)",  11),
        ("ultima_oc",                  "Última OC",      12),
        ("organismos_distintos",       "Organismos",      9),
        ("oportunidad_mensual_MM",     "Oport. est. (MM$)",12),
        ("score",                      "Score",           7),
        ("actividad_actualizada_2026", "Actividad 2026",  35),
        ("por_que_es_prospecto",       "Por qué es prospecto", 55),
    ]

    # Cols de montos para formato especial
    cols_monto = {"monto_total_oc_MM","monto_promedio_oc_MM",
                  "oportunidad_mensual_MM"}
    cols_naranja = {"oportunidad_mensual_MM"}

    # Escribir headers fila 3 (índice 3)
    for ci, (col, label, ancho) in enumerate(columnas):
        fmt = fmt_hdr_naranja if col in cols_naranja else fmt_hdr
        ws.write(3, ci, label, fmt)
        ws.set_column(ci, ci, ancho)

    ws.set_row(3, 30)

    # Escribir datos desde fila 4
    for ri in range(len(df)):
        es_par = ri % 2 == 0
        for ci, (col, label, ancho) in enumerate(columnas):
            val = df.iloc[ri][col] if col in df.columns else ""
            if pd.isna(val):
                val = "-"

            if col == "prioridad":
                fmt_use = fmt_prio_par if es_par else fmt_prio
            elif col in cols_monto:
                fmt_use = fmt_monto_par if es_par else fmt_monto
                try:
                    val = float(val)
                except Exception:
                    val = 0.0
            elif col == "score":
                fmt_use = fmt_score_par if es_par else fmt_score
            else:
                fmt_use = fmt_par if es_par else fmt_norm

            ws.write(ri + 4, ci, val, fmt_use)

        ws.set_row(ri + 4, 22)

    ws.freeze_panes(4, 2)

    # ══════════════════════════════════════════════════════════
    # HOJA 2: RESUMEN EJECUTIVO PARA GG
    # ══════════════════════════════════════════════════════════
    ws2 = wb.add_worksheet("Resumen para GG")

    fmt_t2 = wb.add_format({
        "bold":True,"font_size":16,"font_color":"#1B5E20",
    })
    fmt_sec = wb.add_format({
        "bold":True,"bg_color":"#E8F5E9",
        "font_color":"#1B5E20","border":1,"font_size":11,
    })
    fmt_kpi_lbl = wb.add_format({
        "bold":True,"font_size":13,"align":"right",
        "font_color":"#424242",
    })
    fmt_kpi_val = wb.add_format({
        "bold":True,"font_size":18,"align":"left",
        "font_color":"#1B5E20",
    })
    fmt_bullet = wb.add_format({
        "font_size":11,"text_wrap":True,
    })
    fmt_italic = wb.add_format({
        "italic":True,"font_color":"#666666","font_size":10,
    })

    ws2.set_column(0, 0, 35)
    ws2.set_column(1, 1, 45)

    filas = [
        ("PROPUESTA DE VALOR — BROKER + DATA SCIENCE", None, "titulo"),
        ("Región de Los Lagos | Análisis con datos públicos", None, "sub"),
        ("", None, ""),
        ("QUÉ SE HIZO", None, "seccion"),
        ("Fuentes integradas", "Mercado Público (2022-2026) + SII (feb 2026)", "dato"),
        ("Licitaciones analizadas", "33,816 únicas en Los Lagos", "dato"),
        ("Órdenes de compra", "613,946 únicas procesadas", "dato"),
        ("Empresas evaluadas", "45,774 vigentes en Los Lagos", "dato"),
        ("Modelo de scoring", "8 variables ponderadas (0-100)", "dato"),
        ("", None, ""),
        ("RESULTADOS DEL ANÁLISIS", None, "seccion"),
        ("Prospectos Nivel 1 (contactar hoy)", "81 empresas", "kpi"),
        ("Prospectos Nivel 2 (esta semana)", "821 empresas", "kpi"),
        ("Monto OC top 15 prospectos", "$4,800 millones (solo top 15)", "kpi"),
        ("Empresa más urgente", "SAN SEBASTIAN SPA — $919M en OC activa", "kpi"),
        ("", None, ""),
        ("VENTAJA COMPETITIVA", None, "seccion"),
        ("vs broker tradicional",
         "Llega con datos específicos: monto, fecha OC, "
         "historial. No es llamada en frío.", "bullet"),
        ("vs banco o factoring directo",
         "El broker identifica la necesidad antes de que "
         "el cliente la reconozca. Llega primero.", "bullet"),
        ("vs otro broker con datos",
         "Pipeline automatizado actualizable diariamente "
         "con nueva API y OCDS.", "bullet"),
        ("", None, ""),
        ("PROPUESTA DE CONTINUIDAD", None, "seccion"),
        ("Pipeline actualizable",
         "Script diario: nuevas adjudicaciones → alerta "
         "automática → llamada inmediata", "bullet"),
        ("Modelo predictivo (en desarrollo)",
         "Predice quién va a ganar licitaciones activas "
         "antes de que se adjudiquen", "bullet"),
        ("CRM integrado",
         "Seguimiento de 81+ prospectos con estado, "
         "próximo paso y resultado", "bullet"),
    ]

    fila_y = 0
    for et, val, tipo in filas:
        if tipo == "titulo":
            ws2.write(fila_y, 0, et, fmt_t2)
        elif tipo == "sub":
            ws2.write(fila_y, 0, et, fmt_italic)
        elif tipo == "seccion":
            ws2.write(fila_y, 0, et, fmt_sec)
            ws2.write(fila_y, 1, "", fmt_sec)
        elif tipo == "dato":
            ws2.write(fila_y, 0, et, fmt_kpi_lbl)
            ws2.write(fila_y, 1, val, fmt_bullet)
        elif tipo == "kpi":
            ws2.write(fila_y, 0, et, fmt_kpi_lbl)
            ws2.write(fila_y, 1, val, fmt_kpi_val)
        elif tipo == "bullet":
            ws2.write(fila_y, 0, f"  • {et}", fmt_bullet)
            ws2.write(fila_y, 1, val, fmt_bullet)
            ws2.set_row(fila_y, 30)
        fila_y += 1

    # ══════════════════════════════════════════════════════════
    # HOJA 3: METODOLOGÍA (para mostrar sin revelar datos)
    # ══════════════════════════════════════════════════════════
    ws3 = wb.add_worksheet("Metodología")
    fmt_m = wb.add_format({
        "bold":True,"font_size":13,"font_color":"#1B5E20",
    })
    fmt_ms = wb.add_format({
        "bold":True,"bg_color":"#E3F2FD","font_color":"#0D47A1",
    })
    fmt_md = wb.add_format({"text_wrap":True,"font_size":10})

    ws3.set_column(0, 0, 28)
    ws3.set_column(1, 1, 55)

    met = [
        ("CÓMO SE CONSTRUYÓ EL MODELO", ""),
        ("", ""),
        ("FUENTE 1 — Mercado Público", ""),
        ("datos.chilecompra.cl",
         "51 meses de licitaciones y OC (2022-2026). "
         "Filtrado por Región de Los Lagos. "
         "33,816 licitaciones únicas procesadas."),
        ("API tiempo real",
         "Licitaciones adjudicadas diarias y "
         "licitaciones activas para monitoreo."),
        ("OCDS — oferentes",
         "Empresas que ofertaron pero no ganaron. "
         "Permite predecir ganadores futuros."),
        ("", ""),
        ("FUENTE 2 — SII", ""),
        ("Nómina PJ 2020-2024",
         "Tramo ventas, capital propio, rubro, "
         "antigüedad. 994,476 empresas."),
        ("Razón social feb 2026",
         "Vigencia actualizada. Elimina empresas cerradas. "
         "801 cerradas excluidas en Los Lagos."),
        ("Actividades feb 2026",
         "Giro actualizado para scoring de rubro prioritario."),
        ("", ""),
        ("MODELO DE SCORING — 8 VARIABLES", ""),
        ("Historial adjudicaciones (25%)",
         "Escala log: más licitaciones ganadas = mayor score"),
        ("Tramo ventas SII (20%)",
         "Pequeña y mediana = ideal. Micro = muy chica. "
         "Grande = ya tiene crédito bancario."),
        ("Capital negativo (20%)",
         "Señal directa de necesidad de liquidez. "
         "Capital negativo = opera con deuda."),
        ("Antigüedad empresa (10%)",
         "Entre 3 y 10 años = rango ideal factoring."),
        ("Rubro prioritario (10%)",
         "Acuicultura, construcción, transporte, "
         "servicios = alta demanda factoring."),
        ("Volumen OC (5%)", "Más OC = operación más activa."),
        ("OC reciente <12 meses (5%)",
         "Tiene factura emitida ahora mismo."),
        ("Monto promedio OC (5%)",
         "OC grandes = mayor valor por operación."),
        ("", ""),
        ("CLASIFICACIÓN FINAL", ""),
        ("Nivel 1 — Score ≥ 70 + en MP",
         "81 empresas. Contactar esta semana."),
        ("Nivel 2 — Score 45-69 + en MP",
         "821 empresas. Contactar este mes."),
        ("Nivel 3 — Solo SII",
         "44,872 empresas. Prospección fría futura."),
    ]

    fy = 0
    for et, val in met:
        if et in [
            "CÓMO SE CONSTRUYÓ EL MODELO",
            "FUENTE 1 — Mercado Público",
            "FUENTE 2 — SII",
            "MODELO DE SCORING — 8 VARIABLES",
            "CLASIFICACIÓN FINAL"
        ]:
            ws3.write(fy, 0, et, fmt_ms)
            ws3.write(fy, 1, "", fmt_ms)
        elif et == "":
            pass
        else:
            ws3.write(fy, 0, et, fmt_m if val == "" else None)
            ws3.write(fy, 1, val, fmt_md)
            ws3.set_row(fy, 28)
        fy += 1

print(f"\nExcel ejecutivo generado: {output}")
print("3 hojas:")
print("  1. TOP 15 Prospectos — datos completos")
print("  2. Resumen para GG — propuesta de valor")
print("  3. Metodología — sin revelar datos específicos")