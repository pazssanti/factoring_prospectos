#!/usr/bin/env python3
# =============================================================
#  run_pipeline.py — orquestador completo del pipeline
#
#  Uso:
#    python run_pipeline.py --modo full         ← primera vez
#    python run_pipeline.py --modo incremental  ← cada día
#    python run_pipeline.py --paso ingesta
#    python run_pipeline.py --paso transform
#    python run_pipeline.py --paso score
#    python run_pipeline.py --paso report
#    python run_pipeline.py --paso alertas
#
#  Orden completo:
#    ingesta → transform → score → report
# =============================================================

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from config import init_dirs


# ─────────────────────────────────────────────────────────────
# UTILIDADES DE LOGGING Y EJECUCIÓN
# ─────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def ejecutar_paso(nombre: str, fn) -> bool:
    """
    Ejecuta fn(), loggea inicio/fin/duración.
    Captura cualquier excepción sin propagar.
    Retorna True si OK, False si falló.
    """
    log(f">>  {nombre} ...")
    t0 = time.time()
    try:
        fn()
        dur = round(time.time() - t0, 1)
        log(f"OK  {nombre} -- {dur}s")
        return True
    except Exception as exc:
        dur = round(time.time() - t0, 1)
        log(f"ERR {nombre} FALLO ({dur}s): {exc}")
        return False


def ejecutar_grupo(pasos: list) -> dict:
    """Ejecuta lista de (nombre, fn). Retorna {nombre: bool}."""
    return {nombre: ejecutar_paso(nombre, fn) for nombre, fn in pasos}


def mostrar_resumen(resultados: dict, t_inicio: float):
    ok       = [n for n, v in resultados.items() if v]
    fallidos = [n for n, v in resultados.items() if not v]
    dur      = round(time.time() - t_inicio, 1)

    log("=" * 55)
    log(f"RESUMEN  {len(ok)}/{len(resultados)} OK  --  {dur}s total")
    for n in ok:
        log(f"  OK  {n}")
    for n in fallidos:
        log(f"  ERR {n}")
    if fallidos:
        log(f"  >> {len(fallidos)} paso(s) con error -- revisar salida arriba")
    log("=" * 55)


# ─────────────────────────────────────────────────────────────
# GRUPOS DE PASOS
# ─────────────────────────────────────────────────────────────

def grupo_ingesta_full() -> list:
    """
    Primera ejecución: carga histórica completa.
      - CSV: todos los ZIPs históricos de chilecompra.cl
      - SII: nómina completa de empresas
      - API: últimas 48h (el histórico ya está en los CSV)
      - OCDS: todos los oferentes pendientes (~30-90 min)
    """
    from ingesta.chilecompra_csv    import run as run_csv
    from ingesta.sii_nomina         import run as run_sii
    from ingesta.mercadopublico_api import run as run_api
    from ingesta.ocds_oferentes     import run as run_ocds
    return [
        ("ingesta/chilecompra_csv",    run_csv),
        ("ingesta/sii_nomina",         run_sii),
        ("ingesta/mercadopublico_api", lambda: run_api(modo="incremental")),
        ("ingesta/ocds_oferentes",     lambda: run_ocds(modo="full")),
    ]


def grupo_ingesta_incremental() -> list:
    """
    Actualización diaria: solo actualiza lo que cambia.
      - CSV y SII son estáticos → no se re-ejecutan
      - API: licitaciones y OC de las últimas 48h
      - OCDS: solo oferentes de licitaciones nuevas
    """
    from ingesta.mercadopublico_api import run as run_api
    from ingesta.ocds_oferentes     import run as run_ocds
    return [
        ("ingesta/mercadopublico_api", lambda: run_api(modo="incremental")),
        ("ingesta/ocds_oferentes",     lambda: run_ocds(modo="incremental")),
    ]


def grupo_ingesta_paso() -> list:
    """
    Para --paso ingesta: los 4 módulos con API/OCDS en incremental.
    Usar cuando se quiere refrescar todo sin ser la primera vez.
    """
    from ingesta.chilecompra_csv    import run as run_csv
    from ingesta.sii_nomina         import run as run_sii
    from ingesta.mercadopublico_api import run as run_api
    from ingesta.ocds_oferentes     import run as run_ocds
    return [
        ("ingesta/chilecompra_csv",    run_csv),
        ("ingesta/sii_nomina",         run_sii),
        ("ingesta/mercadopublico_api", lambda: run_api(modo="incremental")),
        ("ingesta/ocds_oferentes",     lambda: run_ocds(modo="incremental")),
    ]


def grupo_transform() -> list:
    """Limpieza → cruce → plazos de pago → construcción de features."""
    from transform.limpiar_licitaciones  import run as run_limpiar
    from transform.cruzar_fuentes        import run as run_cruzar
    from transform.calcular_plazos_pago  import run as run_plazos
    from transform.construir_features    import run as run_features
    return [
        ("transform/limpiar_licitaciones", run_limpiar),
        ("transform/cruzar_fuentes",       run_cruzar),
        ("transform/calcular_plazos_pago", run_plazos),   # nuevo — antes de features
        ("transform/construir_features",   run_features),
    ]


def grupo_score() -> list:
    """Scoring ponderado y ranking de prospectos."""
    from models.scoring_prospecto      import run as run_score
    from models.prediccion_adjudicacion import run as run_pred
    return [
        ("models/scoring_prospecto",       run_score),
        ("models/prediccion_adjudicacion", run_pred),
    ]


def grupo_report() -> list:
    """Exportación del Excel final de prospectos."""
    from reports.exportar_excel import run as run_excel
    return [
        ("reports/exportar_excel", run_excel),
    ]


def grupo_alertas() -> list:
    """Revisión única de OC del día (lo normal lo hace alertas_loop.py)."""
    from ingesta.alertas_tiempo_real import run as run_alertas
    return [
        ("ingesta/alertas_tiempo_real", run_alertas),
    ]


# ─────────────────────────────────────────────────────────────
# MODOS COMPLETOS
# ─────────────────────────────────────────────────────────────

def run_full():
    log("=" * 55)
    log("PIPELINE MODO FULL — primera ejecución completa")
    log("Orden: ingesta → transform → score → report")
    log("=" * 55)
    t0 = time.time()
    resultados = {}
    resultados.update(ejecutar_grupo(grupo_ingesta_full()))
    resultados.update(ejecutar_grupo(grupo_transform()))
    resultados.update(ejecutar_grupo(grupo_score()))
    resultados.update(ejecutar_grupo(grupo_report()))
    mostrar_resumen(resultados, t0)


def run_incremental():
    log("=" * 55)
    log("PIPELINE MODO INCREMENTAL — actualización diaria")
    log("Orden: ingesta(api+ocds) → transform → score → report")
    log("=" * 55)
    t0 = time.time()
    resultados = {}
    resultados.update(ejecutar_grupo(grupo_ingesta_incremental()))
    resultados.update(ejecutar_grupo(grupo_transform()))
    resultados.update(ejecutar_grupo(grupo_score()))
    resultados.update(ejecutar_grupo(grupo_report()))
    mostrar_resumen(resultados, t0)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline de prospección factoring Los Lagos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ejemplos:
  python run_pipeline.py --modo full          primera ejecución completa
  python run_pipeline.py --modo incremental   actualización diaria
  python run_pipeline.py --paso ingesta       solo descarga de datos
  python run_pipeline.py --paso transform     solo limpieza y features
  python run_pipeline.py --paso score         solo scoring y ranking
  python run_pipeline.py --paso report        solo exportar Excel
  python run_pipeline.py --paso alertas       revisar OC del día ahora
        """,
    )
    parser.add_argument(
        "--modo",
        choices=["full", "incremental"],
        default=None,
        help="Pipeline completo (full = primera vez, incremental = diario)",
    )
    parser.add_argument(
        "--paso",
        choices=["ingesta", "transform", "score", "report", "alertas"],
        default=None,
        help="Ejecutar solo un grupo de pasos",
    )

    args = parser.parse_args()

    if not args.modo and not args.paso:
        parser.print_help()
        sys.exit(0)

    init_dirs()
    t_inicio = time.time()

    if args.modo == "full":
        run_full()
    elif args.modo == "incremental":
        run_incremental()
    elif args.paso:
        grupos = {
            "ingesta":   grupo_ingesta_paso,
            "transform": grupo_transform,
            "score":     grupo_score,
            "report":    grupo_report,
            "alertas":   grupo_alertas,
        }
        resultados = ejecutar_grupo(grupos[args.paso]())
        mostrar_resumen(resultados, t_inicio)
