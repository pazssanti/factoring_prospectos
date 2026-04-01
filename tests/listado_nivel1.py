# =============================================================
#  tests/listado_81.py — Versión 2
#  Genera Excel con columnas CRM para mapeo manual
# =============================================================

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/factoring_prospeccion.db")
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql("""
    SELECT
        razon_social                        AS empresa,
        rut_normalizado                     AS rut,
        comuna,
        actividad_economica                 AS actividad,
        tramo_ventas,
        num_trabajadores                    AS trabajadores,
        licitaciones_ganadas,
        total_oc,
        ROUND(monto_total_oc / 1000000, 1)  AS monto_oc_MM,
        ROUND(monto_prom_oc  / 1000000, 2)  AS prom_oc_MM,
        ultima_oc,
        organismos_distintos,
        score,
        motivo
    FROM prospectos_rankeados
    WHERE nivel = '1 - Contactar hoy'
    ORDER BY ultima_oc DESC, monto_total_oc DESC
""", conn)
conn.close()

df = df.reset_index(drop=True)
df.insert(0, "prioridad", df.index + 1)

# ── Columnas manuales de investigación ───────────────────────
df["representante_legal"]      = ""
df["telefono"]                 = ""
df["email"]                    = ""
df["linkedin_rl"]              = ""

# ── Columnas manuales de factoring actual ────────────────────
df["ya_tiene_factoring"]       = ""   # Si / No / No sabe
df["proveedor_factoring"]      = ""   # BCI, Santander, otro
df["porcentaje_anticipo"]      = ""   # ej: 80%, 90%
df["plazo_pago_OC_dias"]       = ""   # 30, 60, 90 dias
df["argumento_especifico"]     = ""   # punto debil detectado

# ── Columnas CRM de seguimiento ───────────────────────────────
df["fecha_primer_contacto"]    = ""
df["canal_contacto"]           = ""   # tel, email, visita
df["resultado_contacto"]       = ""   # No contesta / Interesado / No interesa
df["fecha_reunion"]            = ""
df["resultado_reunion"]        = ""
df["siguiente_paso"]           = ""
df["fecha_siguiente_paso"]     = ""
df["estado_prospecto"]         = ""   # Activo / Cerrado / En proceso
df["notas"]                    = ""

print(f"Empresas Nivel 1: {len(df)}")

output = Path("output/CRM_prospectos_nivel1.xlsx")
output.parent.mkdir(exist_ok=True)

with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
    df.to_excel(writer, sheet_name="Prospectos", index=False)
    wb = writer.book
    ws = writer.sheets["Prospectos"]

    # ── Formatos ──────────────────────────────────────────────
    fmt_hdr_datos = wb.add_format({
        "bold": True, "bg_color": "#1B5E20",
        "font_color": "white", "border": 1,
        "align": "center", "text_wrap": True,
    })
    fmt_hdr_invest = wb.add_format({
        "bold": True, "bg_color": "#0D47A1",
        "font_color": "white", "border": 1,
        "align": "center", "text_wrap": True,
    })
    fmt_hdr_factoring = wb.add_format({
        "bold": True, "bg_color": "#E65100",
        "font_color": "white", "border": 1,
        "align": "center", "text_wrap": True,
    })
    fmt_hdr_crm = wb.add_format({
        "bold": True, "bg_color": "#4A148C",
        "font_color": "white", "border": 1,
        "align": "center", "text_wrap": True,
    })
    fmt_dato_par  = wb.add_format({
        "bg_color": "#F5F5F5", "border": 1,
    })
    fmt_dato_norm = wb.add_format({"border": 1})
    fmt_invest    = wb.add_format({
        "bg_color": "#E3F2FD", "border": 1,
    })
    fmt_factoring = wb.add_format({
        "bg_color": "#FFF3E0", "border": 1,
    })
    fmt_crm = wb.add_format({
        "bg_color": "#F3E5F5", "border": 1,
    })

    # ── Grupos de columnas ────────────────────────────────────
    cols_datos = [
        "prioridad","empresa","rut","comuna","actividad",
        "tramo_ventas","trabajadores","licitaciones_ganadas",
        "total_oc","monto_oc_MM","prom_oc_MM",
        "ultima_oc","organismos_distintos","score","motivo",
    ]
    cols_invest = [
        "representante_legal","telefono","email","linkedin_rl",
    ]
    cols_factoring = [
        "ya_tiene_factoring","proveedor_factoring",
        "porcentaje_anticipo","plazo_pago_OC_dias",
        "argumento_especifico",
    ]
    cols_crm = [
        "fecha_primer_contacto","canal_contacto",
        "resultado_contacto","fecha_reunion",
        "resultado_reunion","siguiente_paso",
        "fecha_siguiente_paso","estado_prospecto","notas",
    ]

    # Escribir headers con colores según grupo
    for ci, col in enumerate(df.columns):
        if col in cols_datos:
            ws.write(0, ci, col, fmt_hdr_datos)
        elif col in cols_invest:
            ws.write(0, ci, col, fmt_hdr_invest)
        elif col in cols_factoring:
            ws.write(0, ci, col, fmt_hdr_factoring)
        else:
            ws.write(0, ci, col, fmt_hdr_crm)

    # Escribir datos con colores según tipo de columna
    for ri in range(len(df)):
        for ci, col in enumerate(df.columns):
            val = df.iloc[ri][col]
            val = "" if pd.isna(val) else val
            if col in cols_invest:
                ws.write(ri + 1, ci, val, fmt_invest)
            elif col in cols_factoring:
                ws.write(ri + 1, ci, val, fmt_factoring)
            elif col in cols_crm:
                ws.write(ri + 1, ci, val, fmt_crm)
            elif ri % 2 == 0:
                ws.write(ri + 1, ci, val, fmt_dato_par)
            else:
                ws.write(ri + 1, ci, val, fmt_dato_norm)

    # Anchos de columna
    anchos = {
        "prioridad": 6, "empresa": 45, "rut": 12,
        "comuna": 14, "actividad": 35, "tramo_ventas": 8,
        "trabajadores": 8, "licitaciones_ganadas": 8,
        "total_oc": 6, "monto_oc_MM": 10, "prom_oc_MM": 10,
        "ultima_oc": 14, "organismos_distintos": 8,
        "score": 6, "motivo": 55,
        "representante_legal": 25, "telefono": 14,
        "email": 28, "linkedin_rl": 28,
        "ya_tiene_factoring": 12, "proveedor_factoring": 18,
        "porcentaje_anticipo": 12, "plazo_pago_OC_dias": 10,
        "argumento_especifico": 35,
        "fecha_primer_contacto": 14, "canal_contacto": 12,
        "resultado_contacto": 18, "fecha_reunion": 14,
        "resultado_reunion": 20, "siguiente_paso": 25,
        "fecha_siguiente_paso": 14, "estado_prospecto": 14,
        "notas": 35,
    }
    for ci, col in enumerate(df.columns):
        ws.set_column(ci, ci, anchos.get(col, 15))

    # Freeze primera fila y primeras 3 columnas
    ws.freeze_panes(1, 3)

    # Fila de altura para el header
    ws.set_row(0, 35)

    # ── Hoja de instrucciones ─────────────────────────────────
    ws2 = wb.add_worksheet("Instrucciones")
    fmt_t = wb.add_format({
        "bold": True, "font_size": 14,
        "font_color": "#1B5E20",
    })
    fmt_s = wb.add_format({
        "bold": True, "bg_color": "#E8F5E9",
    })
    fmt_n = wb.add_format({"text_wrap": True})

    instrucciones = [
        ("GUIA DE USO — CRM PROSPECTOS FACTORING", ""),
        ("", ""),
        ("COLORES DE COLUMNAS", ""),
        ("Verde oscuro", "Datos del sistema — no editar"),
        ("Azul", "Investigacion manual (RL, tel, email)"),
        ("Naranja", "Estado del factoring actual"),
        ("Morado", "Seguimiento CRM"),
        ("", ""),
        ("DONDE OBTENER CADA DATO", ""),
        ("Representante legal",
         "registrodeempresasysociedades.cl → buscar por RUT"),
        ("Telefono / Email",
         "Google + sitio web empresa + hunter.io"),
        ("LinkedIn RL",
         "linkedin.com → buscar nombre del RL"),
        ("Ya tiene factoring",
         "Google: nombre empresa + factoring"),
        ("Proveedor factoring",
         "Google o preguntar directamente en la llamada"),
        ("Porcentaje anticipo",
         "Preguntar en la llamada: que % les adelantan?"),
        ("Plazo pago OC dias",
         "mercadopublico.cl → buscar OC → ver condiciones pago"),
        ("Argumento especifico",
         "Punto debil detectado: tasa alta, plazo largo, % bajo"),
        ("", ""),
        ("ESTADOS DE PROSPECTO", ""),
        ("Activo", "En proceso de contacto o negociacion"),
        ("En proceso", "Reunion agendada o propuesta enviada"),
        ("Cerrado ganado", "Se convirtio en cliente"),
        ("Cerrado perdido", "No intereso o ya tiene factoring fijo"),
        ("En espera", "Volver a contactar en fecha futura"),
        ("", ""),
        ("ORDEN DE PRIORIDAD RECOMENDADO", ""),
        ("Prioridades 1-15",
         "Llamar esta semana — OC > $80M en marzo 2026"),
        ("Prioridades 16-38",
         "Llamar proxima semana — OC activa en 2026"),
        ("Prioridades 39-55",
         "Segunda quincena — OC activa en 2025"),
        ("Prioridades 56+",
         "Baja urgencia — OC antigua o monto bajo"),
        ("", ""),
        ("ARGUMENTO POR TELEFONO", ""),
        ("Apertura",
         "Buenos dias, hablo con [nombre RL]? "
         "Soy [tu nombre], broker de [factoring]. "
         "Vi que [empresa] trabaja con organismos publicos "
         "de la region..."),
        ("Propuesta",
         "...y queria saber si han considerado el factoring "
         "para mejorar el flujo mientras esperan el pago "
         "del Estado. Con nosotros pueden tener el dinero "
         "en 48 horas."),
        ("Si ya tienen factoring",
         "Que porcentaje de la factura les anticipan "
         "actualmente? Podemos evaluar si podemos "
         "mejorar esa condicion."),
    ]

    for i, (et, val) in enumerate(instrucciones):
        if i == 0:
            ws2.write(i, 0, et, fmt_t)
        elif val == "" and et == "":
            pass
        elif val == "" and et != "":
            ws2.write(i, 0, et, fmt_s)
            ws2.write(i, 1, val, fmt_s)
        else:
            ws2.write(i, 0, et)
            ws2.write(i, 1, val, fmt_n)

    ws2.set_column(0, 0, 28)
    ws2.set_column(1, 1, 65)

print(f"\nExcel CRM guardado: {output}")
print("Tiene 2 hojas:")
print("  1. Prospectos — las 81 empresas con columnas CRM")
print("  2. Instrucciones — guia de uso y argumentos")