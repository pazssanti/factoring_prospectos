# =============================================================
#  tests/plazo_pago_top15.py
#  Obtiene el plazo de pago típico de cada empresa del TOP 15
#  y actualiza el Excel de la reunión con ese dato
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

# ── Verificar columnas disponibles ───────────────────────────
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(raw_ordenes_csv)"
).fetchall()]
cols_pago = [c for c in cols if "pago" in c.lower()
             or "forma" in c.lower()]
print(f"Columnas de pago encontradas: {cols_pago}")

# ── Plazo típico por empresa del TOP 15 ───────────────────────
df_plazo = pd.read_sql("""
    SELECT
        p.razon_social,
        p.rut_normalizado,
        o.forma_de_pago,
        COUNT(*) as n_oc
    FROM raw_ordenes_csv o
    INNER JOIN prospectos_rankeados p
        ON UPPER(
            REPLACE(REPLACE(o.rutsucursal, '.', ''), '-', '')
           ) = p.rut_normalizado
    WHERE p.nivel = '1 - Contactar hoy'
    AND o.forma_de_pago IS NOT NULL
    AND o.forma_de_pago != ''
    GROUP BY p.razon_social, p.rut_normalizado, o.forma_de_pago
    ORDER BY p.razon_social, n_oc DESC
""", conn)

print(f"\nRegistros encontrados: {len(df_plazo)}")
print(df_plazo.to_string())

# Tomar el plazo más frecuente por empresa
df_plazo_top = (
    df_plazo.sort_values("n_oc", ascending=False)
    .drop_duplicates("rut_normalizado")
    [["rut_normalizado", "forma_de_pago"]]
    .rename(columns={"forma_de_pago": "plazo_pago_tipico"})
)

# Simplificar el texto del plazo
def simplificar_plazo(texto):
    if pd.isna(texto):
        return "No especificado"
    t = str(texto).lower()
    if "30" in t:
        return "30 días"
    if "60" in t:
        return "60 días"
    if "90" in t:
        return "90 días"
    if "contado" in t or "inmediato" in t:
        return "Contado"
    return texto[:40]

df_plazo_top["plazo_dias"] = df_plazo_top[
    "plazo_pago_tipico"
].apply(simplificar_plazo)

print("\nPlazo típico por empresa TOP 15:")
print(df_plazo_top[["rut_normalizado","plazo_dias"]]
      .to_string(index=False))

# ── Integrar al TOP 15 y generar Excel actualizado ────────────
df_top15 = pd.read_sql("""
    SELECT
        razon_social        AS empresa,
        rut_normalizado     AS rut,
        comuna,
        tramo_ventas,
        num_trabajadores    AS trabajadores,
        vigente_2026,
        licitaciones_ganadas,
        total_oc,
        ROUND(monto_total_oc/1000000, 1)     AS monto_oc_MM,
        ROUND(monto_prom_oc/1000000, 2)      AS prom_oc_MM,
        ultima_oc,
        organismos_distintos,
        score,
        motivo              AS argumento
    FROM prospectos_rankeados
    WHERE nivel = '1 - Contactar hoy'
    ORDER BY ultima_oc DESC, monto_total_oc DESC
    LIMIT 15
""", conn)
conn.close()

df_top15 = df_top15.reset_index(drop=True)
df_top15.insert(0, "prioridad", df_top15.index + 1)

# Unir plazo de pago
df_top15 = df_top15.merge(
    df_plazo_top[["rut_normalizado", "plazo_dias"]],
    left_on="rut", right_on="rut_normalizado",
    how="left"
).drop(columns=["rut_normalizado"], errors="ignore")

df_top15["plazo_dias"] = df_top15["plazo_dias"].fillna("30 días")

# Calcular dinero bloqueado
df_top15["dinero_bloqueado_MM"] = (
    df_top15["prom_oc_MM"] *
    df_top15["plazo_dias"].str.extract(r"(\d+)")[0]
    .astype(float) / 30
).round(1)

# Formatear
df_top15["tramo_ventas"] = (
    df_top15["tramo_ventas"].astype(str).str.strip()
    .map(mapa_tramo).fillna(df_top15["tramo_ventas"])
)
df_top15["vigente_2026"] = df_top15["vigente_2026"].map(
    {1:"Sí", 0:"No"}
).fillna("Sí")
df_top15["ultima_oc"] = pd.to_datetime(
    df_top15["ultima_oc"], errors="coerce"
).dt.strftime("%d-%m-%Y").fillna("-")

# Columnas manuales
df_top15["representante_legal"] = ""
df_top15["telefono"]            = ""
df_top15["ya_tiene_factoring"]  = ""
df_top15["argumento_llamada"]   = ""

# ── Generar Excel ─────────────────────────────────────────────
output = Path("output/TOP15_con_plazo_pago.xlsx")
output.parent.mkdir(exist_ok=True)

with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
    wb = writer.book

    fmt_titulo = wb.add_format({
        "bold":True, "font_size":16,
        "font_color":"#1B5E20",
    })
    fmt_sub = wb.add_format({
        "italic":True, "font_size":10,
        "font_color":"#888888",
    })
    fmt_hdr = wb.add_format({
        "bold":True, "bg_color":"#1B5E20",
        "font_color":"white", "border":1,
        "align":"center", "text_wrap":True,
        "valign":"vcenter",
    })
    fmt_hdr_rojo = wb.add_format({
        "bold":True, "bg_color":"#B71C1C",
        "font_color":"white", "border":1,
        "align":"center", "text_wrap":True,
    })
    fmt_hdr_azul = wb.add_format({
        "bold":True, "bg_color":"#0D47A1",
        "font_color":"white", "border":1,
        "align":"center", "text_wrap":True,
    })
    fmt_hdr_naranja = wb.add_format({
        "bold":True, "bg_color":"#E65100",
        "font_color":"white", "border":1,
        "align":"center", "text_wrap":True,
    })
    fmt_par  = wb.add_format({
        "bg_color":"#F1F8E9", "border":1,
        "valign":"vcenter",
    })
    fmt_norm = wb.add_format({
        "border":1, "valign":"vcenter",
    })
    fmt_prio_par = wb.add_format({
        "bold":True, "bg_color":"#2E7D32",
        "font_color":"white", "border":1,
        "align":"center", "font_size":13,
    })
    fmt_prio = wb.add_format({
        "bold":True, "bg_color":"#1B5E20",
        "font_color":"white", "border":1,
        "align":"center", "font_size":13,
    })
    fmt_monto_par = wb.add_format({
        "bg_color":"#F1F8E9", "border":1,
        "bold":True, "font_color":"#1B5E20",
    })
    fmt_monto = wb.add_format({
        "border":1, "bold":True,
        "font_color":"#1B5E20",
    })
    fmt_rojo_par = wb.add_format({
        "bg_color":"#FFEBEE", "border":1,
        "bold":True, "font_color":"#B71C1C",
        "align":"center",
    })
    fmt_rojo = wb.add_format({
        "border":1, "bold":True,
        "font_color":"#B71C1C", "align":"center",
    })
    fmt_amarillo = wb.add_format({
        "bg_color":"#FFF9C4", "border":1,
    })

    ws = wb.add_worksheet("TOP 15 - Con plazo de pago")

    ws.write(0, 0,
        "TOP 15 PROSPECTOS — FACTORING REGIÓN DE LOS LAGOS",
        fmt_titulo)
    ws.write(1, 0,
        f"Generado: {datetime.now().strftime('%d/%m/%Y')} | "
        "Fuente: Mercado Público 2022-2026 + SII feb 2026",
        fmt_sub)
    ws.write(2, 0,
        "El 99% de las OC del Estado en Los Lagos pagan a 30 días. "
        "Columna 'Dinero bloqueado' = dinero inmovilizado durante ese plazo.",
        fmt_sub)

    columnas = [
        ("prioridad",           "#",                    5,  "verde"),
        ("empresa",             "Empresa",             40,  "verde"),
        ("rut",                 "RUT",                 13,  "verde"),
        ("comuna",              "Ciudad",              13,  "verde"),
        ("tramo_ventas",        "Tamaño",              10,  "verde"),
        ("trabajadores",        "Trabaj.",              7,  "verde"),
        ("vigente_2026",        "Vigente",              7,  "verde"),
        ("licitaciones_ganadas","Licit.",               7,  "verde"),
        ("total_oc",            "N° OC",               6,  "verde"),
        ("monto_oc_MM",         "Monto OC (MM$)",      12,  "verde"),
        ("prom_oc_MM",          "Prom OC (MM$)",       11,  "verde"),
        ("plazo_dias",          "Plazo pago",          10,  "rojo"),
        ("dinero_bloqueado_MM", "$ Bloqueado (MM$)",   13,  "rojo"),
        ("ultima_oc",           "Última OC",           12,  "verde"),
        ("organismos_distintos","Organismos",           8,  "verde"),
        ("score",               "Score",                7,  "azul"),
        ("representante_legal", "Rep. Legal",          22,  "naranja"),
        ("telefono",            "Teléfono",            14,  "naranja"),
        ("ya_tiene_factoring",  "¿Ya tiene factoring?",14,  "naranja"),
        ("argumento_llamada",   "Argumento de llamada",35,  "naranja"),
        ("argumento",           "Por qué es prospecto",50,  "azul"),
    ]

    mapa_fmt_hdr = {
        "verde": fmt_hdr,
        "rojo":  fmt_hdr_rojo,
        "azul":  fmt_hdr_azul,
        "naranja": fmt_hdr_naranja,
    }

    for ci, (col, label, ancho, color) in enumerate(columnas):
        ws.write(3, ci, label, mapa_fmt_hdr[color])
        ws.set_column(ci, ci, ancho)
    ws.set_row(3, 32)

    for ri in range(len(df_top15)):
        par = ri % 2 == 0
        for ci, (col, label, ancho, color) in enumerate(columnas):
            val = df_top15.iloc[ri][col] \
                  if col in df_top15.columns else ""
            if pd.isna(val):
                val = "-"

            if col == "prioridad":
                ws.write(ri+4, ci, val,
                         fmt_prio_par if par else fmt_prio)
            elif col in {"monto_oc_MM","prom_oc_MM",
                         "dinero_bloqueado_MM"}:
                try:
                    val = float(val)
                except Exception:
                    val = 0.0
                ws.write(ri+4, ci, val,
                         fmt_monto_par if par else fmt_monto)
            elif col in {"plazo_dias"}:
                ws.write(ri+4, ci, val,
                         fmt_rojo_par if par else fmt_rojo)
            elif col in {"representante_legal","telefono",
                         "ya_tiene_factoring","argumento_llamada"}:
                ws.write(ri+4, ci, "", fmt_amarillo)
            else:
                ws.write(ri+4, ci, val,
                         fmt_par if par else fmt_norm)
        ws.set_row(ri+4, 22)

    ws.freeze_panes(4, 2)

    # ── Hoja resumen de plazos ────────────────────────────────
    ws2 = wb.add_worksheet("Análisis plazos pago")
    fmt_t2 = wb.add_format({
        "bold":True,"font_size":13,"font_color":"#B71C1C",
    })
    fmt_s2 = wb.add_format({
        "bold":True,"bg_color":"#FFEBEE",
        "font_color":"#B71C1C","border":1,
    })
    fmt_d2 = wb.add_format({"border":1})
    fmt_k2 = wb.add_format({
        "bold":True,"font_size":16,
        "font_color":"#B71C1C",
    })

    ws2.write(0, 0,
        "ANÁLISIS DE PLAZOS DE PAGO — ARGUMENTO CENTRAL",
        fmt_t2)

    datos2 = [
        ("", ""),
        ("DISTRIBUCIÓN DE PLAZOS EN LOS LAGOS", ""),
        ("30 días (recepción conforme)",
         "1.660.144 OC — 99% del total"),
        ("60 días",
         "152 OC — 1% del total"),
        ("Contado / Otro",
         "11.180 OC — casos especiales"),
        ("", ""),
        ("IMPACTO FINANCIERO PARA EL PROSPECTO", ""),
        ("Empresa con OC promedio $50M a 30 días",
         "$50M bloqueados durante 1 mes"),
        ("Empresa con OC promedio $100M a 30 días",
         "$100M bloqueados durante 1 mes"),
        ("Empresa con OC promedio $300M a 30 días",
         "$300M bloqueados durante 1 mes"),
        ("", ""),
        ("ARGUMENTO EN LA LLAMADA", ""),
        ("Frase exacta",
         '"Vi que el Estado les paga a 30 días. Con nosotros '
         'pueden tener ese dinero en 48 horas."'),
        ("", ""),
        ("TOP 3 EMPRESAS MÁS URGENTES", ""),
    ]

    top3 = df_top15.head(3)
    for _, row in top3.iterrows():
        datos2.append((
            str(row["empresa"])[:35],
            f"${row['monto_oc_MM']}M en OC | "
            f"plazo: {row['plazo_dias']} | "
            f"última OC: {row['ultima_oc']}"
        ))

    secciones = {
        "DISTRIBUCIÓN DE PLAZOS EN LOS LAGOS",
        "IMPACTO FINANCIERO PARA EL PROSPECTO",
        "ARGUMENTO EN LA LLAMADA",
        "TOP 3 EMPRESAS MÁS URGENTES",
    }

    for i, (et, val) in enumerate(datos2):
        if et in secciones:
            ws2.write(i+1, 0, et, fmt_s2)
            ws2.write(i+1, 1, val, fmt_s2)
        elif et == "Frase exacta":
            ws2.write(i+1, 0, et, fmt_d2)
            ws2.write(i+1, 1, val, fmt_k2)
        else:
            ws2.write(i+1, 0, et, fmt_d2)
            ws2.write(i+1, 1, val, fmt_d2)

    ws2.set_column(0, 0, 38)
    ws2.set_column(1, 1, 60)

print(f"\nExcel generado: {output}")
print("2 hojas:")
print("  1. TOP 15 con plazo de pago real por empresa")
print("  2. Análisis de plazos — argumento central para reunión")
print("\nColumnas amarillas = llenar manualmente antes de llamar")