# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Contexto y objetivo de negocio

Sistema de data science para prospección de clientes de factoring en Los Lagos, Chile. El objetivo es generar leads de alta calidad contactando empresas en el momento óptimo:

- **ANTES del cierre de licitación:** el modelo RandomForest predice quién va a ganar (máxima ventaja competitiva)
- **ENTRE adjudicación y OC:** empresa ya sabe que ganó pero no ha recibido el pago (ventana clave)
- **AL EMITIRSE la OC:** último momento, pero sigue siendo útil

Fuentes de datos activas:
- ZIPs mensuales de datos.chilecompra.cl — fuente principal histórica, confiable (2022-2026)
- API REST de Mercado Público — alertas en tiempo real + licitaciones activas (inestable, requiere resiliencia)
- API OCDS `/award/{id}` — todos los oferentes (ganadores y perdedores) de cada licitación, clave para el modelo ML (inestable)
- Nóminas SII — tramo ventas, capital propio, actividad económica de empresas (datos Feb 2026)

## Fechas y cobertura de los datos

### Datos históricos (base del modelo y scoring)
| Fuente | Archivo | Cobertura |
|---|---|---|
| Licitaciones CSV | `data/raw/licitaciones/2022-1.zip` … `2026-3.zip` | Ene 2022 → Mar 2026 |
| Órdenes de compra CSV | `data/raw/ordenes_compra/2022-1.zip` … `2026-3.zip` | Ene 2022 → Mar 2026 |
| Nómina SII (ventas/capital) | `data/raw/nomina_sii.txt` | Años comerciales 2020-2024 |
| Nómina SII actividades | `data/raw/nomina_sii_actividades.txt` | Feb 2026 |
| Nómina SII razón social | `data/raw/nomina_sii_razon_social.txt` | Feb 2026 |

### Modelo predictivo ML
- **Entrenamiento:** licitaciones con `fechaadjudicacion < 2025-01-01` (~4 años de historia)
- **Test temporal (out-of-time):** licitaciones con `fechaadjudicacion >= 2025-01-01`
- **Modelo en producción:** `data/modelo_adjudicacion.pkl` (RandomForest 200 árboles, max_depth=8)

### Licitaciones activas (sin resolución aún)
Se obtienen en tiempo real desde la API de Mercado Público con `estado=publicada`. El flujo es:
1. `mercadopublico_api.py` con `modo='activas'` → guarda en `raw_licitaciones_activas`
2. `ingesta/enriquecer_licitaciones_activas.py` → filtra Los Lagos, predice ganadores con el modelo → `licitaciones_activas_lagos`, `predicciones_licitaciones_lagos`

Los ZIPs mensuales NO contienen licitaciones activas (solo licitaciones ya cerradas/adjudicadas que el Estado publica mensualmente). La API es la única fuente de licitaciones sin resolución.

**Actualización recomendada:** correr `--modo incremental` a diario. Los ZIPs históricos son estáticos y solo necesitan recargarse si se agregan nuevos meses.

### Desfase real observado de cada fuente de datos (medido al 31-03-2026)
| Fuente | Tabla | Dato más reciente | Desfase |
|---|---|---|---|
| CSVs procesados (OC) | `clean_ordenes` | 2026-03-13 | **~18 días** |
| API descargada (OC) | `raw_ordenes_api` | 2026-03-28 | ~3 días |
| API licitaciones activas | `raw_licitaciones_activas` | 2026-03-30 | ~1 día |
| API en vivo (alertas) | directo | hoy | 0 días |

El desfase del CSV varía entre 13 y 20 días según el ciclo de publicación del Estado.

## Stack tecnológico

Python 3.x, SQLite (`data/factoring_prospeccion.db`), pandas, numpy, xlsxwriter, openpyxl, requests, scikit-learn, python-dotenv

## Ejecutar el pipeline

### Pipeline principal (run_pipeline.py)
```bash
python run_pipeline.py --modo full          # Primera ejecución completa (varias horas)
python run_pipeline.py --modo incremental   # Actualización diaria (API + OCDS + todo el resto)

python run_pipeline.py --paso ingesta       # Solo descarga de datos (CSV + SII + API + OCDS)
python run_pipeline.py --paso transform     # Solo limpieza y features
python run_pipeline.py --paso score         # Solo scoring y modelo predictivo
python run_pipeline.py --paso report        # Solo exportar Excel principal
python run_pipeline.py --paso alertas       # Revisión única de OC del día
```

### Pipeline ML (correr en orden, antes de --paso score si el modelo no existe)
```bash
python models/construir_dataset_training.py   # Construye training_dataset desde raw_oferentes x clean_licitaciones
python models/prediccion_adjudicacion.py      # Entrena RandomForest, guarda modelo_adjudicacion.pkl
python models/validar_modelo.py               # Validación temporal (train <2025, test >=2025) — opcional
```

### Licitaciones activas y predicción de ganadores (manual, cuando se quiera)
```bash
python ingesta/mercadopublico_api.py activas           # Descarga licitaciones publicadas ahora
python ingesta/enriquecer_licitaciones_activas.py      # Filtra Los Lagos + predice ganadores
```

### Alertas en tiempo real (terminal separada, dejar corriendo)
```bash
python alertas_loop.py   # Loop cada 30 min de 8-18 h — detecta OC nuevas y predicciones de alta P(win)
```

### Scripts de análisis y reportes ad-hoc (ejecutar desde la raíz)
```bash
python tests/consulta_prospectos.py    # Filtros inteligentes sobre prospectos_rankeados
python tests/listado_nivel1.py         # Excel CRM con columnas de seguimiento manual
python tests/top15_reunion.py          # Excel ejecutivo 3 hojas para reunión GG
python tests/plazo_pago_top15.py       # Análisis plazos de pago del TOP 15
python output/_gen_resumen.py          # Excel resumen_reunion.xlsx con KPIs y argumentos ejecutivos
```

## Arquitectura y flujo de datos

La DB SQLite es el estado compartido entre todos los scripts:

```
FUENTES EXTERNAS            TABLAS RAW (DB)               TABLAS INTERMEDIAS / OUTPUT
────────────────            ───────────────               ───────────────────────────
ZIPs chilecompra.cl     →  raw_licitaciones_csv       →
                            raw_ordenes_csv            →  clean_licitaciones
API Mercado Público     →  raw_licitaciones_api       →  clean_ordenes
                            raw_ordenes_api            →
                            raw_licitaciones_activas   →  licitaciones_activas_lagos
OCDS /award/{id}        →  raw_oferentes              →  training_dataset
TXT del SII             →  raw_empresas_sii           →

                            FEATURES / SCORING / PREDICCIÓN
                            ────────────────────────────────
raw_oferentes           →  training_dataset           →  modelo_adjudicacion.pkl
clean_licitaciones      →
clean_ordenes           →  features_prospectos        →  prospectos_rankeados  →  Excel
raw_empresas_sii        →
licitaciones_activas_lagos + modelo.pkl  →  predicciones_licitaciones_lagos
```

Cada script de producción expone `def run()` como punto de entrada y funciona standalone vía `if __name__ == "__main__"`.

## Inventario de scripts por carpeta

### Raíz
| Script | Propósito |
|---|---|
| `run_pipeline.py` | Orquestador principal. Modos: `full`, `incremental`, y por `--paso` |
| `alertas_loop.py` | Loop de terminal: llama a `alertas_tiempo_real.run()` cada 30 min de 8-18 h |
| `config.py` | Rutas, constantes API, niveles de score. Tiene efecto secundario: crea carpetas al importar |

### ingesta/
| Script | Inputs | Outputs (tablas DB) |
|---|---|---|
| `chilecompra_csv.py` | ZIPs `data/raw/licitaciones/` y `ordenes_compra/` | `raw_licitaciones_csv`, `raw_ordenes_csv` |
| `sii_nomina.py` | TXTs `data/raw/nomina_sii*.txt` | `raw_empresas_sii` |
| `mercadopublico_api.py` | API MP (modo incremental o activas) | `raw_licitaciones_api`, `raw_ordenes_api`, `raw_licitaciones_activas` |
| `ocds_oferentes.py` | API OCDS `/award/{id}` | `raw_oferentes` |
| `alertas_tiempo_real.py` | DB + API MP | `output/alertas_adjudicaciones.xlsx` (3 hojas) |
| `enriquecer_licitaciones_activas.py` | `raw_licitaciones_activas` + `modelo_adjudicacion.pkl` | `licitaciones_activas_lagos`, `predicciones_licitaciones_lagos` |

### transform/
| Script | Inputs | Outputs |
|---|---|---|
| `limpiar_licitaciones.py` | `raw_licitaciones_csv`, `raw_licitaciones_api` | `clean_licitaciones` |
| `cruzar_fuentes.py` | `clean_licitaciones`, `raw_ordenes_*`, `raw_empresas_sii` | `clean_ordenes`, `clean_proveedores` |
| `construir_features.py` | `clean_licitaciones`, `clean_ordenes`, `raw_empresas_sii` | `features_prospectos` |

### models/
| Script | Propósito | Integrado en run_pipeline? |
|---|---|---|
| `scoring_prospecto.py` | Scoring 8 features → `prospectos_rankeados` | Sí (`--paso score`) |
| `prediccion_adjudicacion.py` | Entrena RandomForest con `training_dataset`, guarda `.pkl`, genera `predicciones_activas` | Sí (`--paso score`) |
| `construir_dataset_training.py` | Construye `training_dataset` cruzando `raw_oferentes` × `clean_licitaciones` × SII | **No** — correr manualmente antes del primer entrenamiento |
| `validar_modelo.py` | Validación temporal out-of-time (train<2025, test≥2025), imprime métricas | **No** — correr manualmente para evaluar el modelo |

### reports/
| Script | Inputs | Outputs |
|---|---|---|
| `exportar_excel.py` | `prospectos_rankeados` | `output/prospectos_factoring.xlsx` (4 hojas) |

### tests/ (scripts de análisis, NO tests de pytest)
| Script | Genera |
|---|---|
| `consulta_prospectos.py` | Filtros inteligentes en terminal (ideal/alto-valor/nuevos) |
| `listado_nivel1.py` | `output/CRM_prospectos_nivel1.xlsx` — columnas para seguimiento manual |
| `top15_reunion.py` | Excel ejecutivo TOP 15 para reunión |
| `plazo_pago_top15.py` | Análisis de plazos de pago del TOP 15 |

### output/ (scripts generadores)
| Script | Genera |
|---|---|
| `_gen_resumen.py` | `output/resumen_reunion.xlsx` — 3 hojas: TOP 10, urgentes, resumen ejecutivo con KPIs |

## Modelo de scoring actual

8 features (0–100 c/u), pesos definidos en `models/scoring_prospecto.py` (dict `PESOS`):

| Feature | Peso | Fuente |
|---|---|---|
| f_historial (licitaciones ganadas) | 25% | clean_licitaciones |
| f_tramo_ventas (tramo SII) | 20% | raw_empresas_sii |
| f_capital_negativo (capital propio negativo) | 20% | raw_empresas_sii |
| f_antiguedad (antigüedad empresa) | 10% | raw_empresas_sii |
| f_rubro_prioritario (rubro afín) | 10% | raw_empresas_sii |
| f_volumen_oc (cantidad de OC) | 5% | clean_ordenes |
| f_oc_reciente (OC en últimos 12 meses) | 5% | clean_ordenes |
| f_monto_oc (monto promedio OC) | 5% | clean_ordenes |

`SCORE_NIVEL_1 = 70` → "Contactar hoy" | `SCORE_NIVEL_2 = 45` → "Contactar esta semana". Configurados en `config.py`.

**Atención:** `config.py` define constantes `PESO_HISTORIAL`, `PESO_VENTAS`, etc., que **ningún script importa** — son dead code que no controla el modelo real.

## Modelo predictivo ML (prediccion_adjudicacion.py)

RandomForest (200 árboles, max_depth=8, class_weight=balanced) entrenado sobre `training_dataset`:

Features del modelo:
- `n_oferentes` (competidores en la licitación)
- `region_empresa` == `region_licitacion` (empresa local)
- `tramo_ventas` (tamaño empresa SII)
- `tramo_capital_negativo` (indicador de tensión financiera)
- `licitaciones_ganadas` (historial del proveedor)
- `es_convenio_marco` (tipo LR vs licitación normal)

Label: `gano=1` si el proveedor ganó (por CSV o OCDS, para capturar multi-ganadores).

**Para predecir ganadores en licitaciones ACTIVAS:** correr `enriquecer_licitaciones_activas.py` — hace cross-join empresa × licitación abierta y aplica el modelo.

## Convenciones obligatorias

- Importar rutas desde `config.py`, nunca hardcodear paths
- Usar `normalizar_rut()` desde `utils/helpers.py`
- Usar `mapa_tramo` desde `utils/helpers.py`
- Credenciales en `.env` y `os.environ.get()`, nunca en el código
- Todas las funciones principales deben exponer `def run()` como punto de entrada
- Requests a APIs externas deben usar `get_con_reintento()` desde `utils/helpers.py`
- Cada script nuevo debe tener cabecera con: propósito, inputs, outputs, tablas SQLite que produce

## Qué NUNCA hacer

- No modificar archivos en `data/raw/`
- No hardcodear `TICKET_API` ni ninguna credencial
- No duplicar `normalizar_rut()` ni `mapa_tramo` — viven en `utils/helpers.py`
- No usar `subprocess` para llamar scripts propios — usar `from modulo import run`
- No crear archivos Excel con datos hardcodeados en el código

## Gotchas importantes

- **`tests/` no son tests de pytest** — son scripts de análisis y reportes. Ejecutar desde la raíz del proyecto.
- **`output/_gen_resumen.py`** es también un script generador (no un módulo), no expone `run()`.
- **Scripts en subcarpetas** requieren `sys.path.append(str(Path(__file__).resolve().parent.parent))` — no hay `setup.py`.
- **`config.py` tiene efecto secundario al importar**: crea directorios automáticamente.
- **`models/construir_dataset_training.py` y `models/validar_modelo.py`** NO están integrados en `run_pipeline.py` — correr manualmente cuando se quiera re-entrenar o evaluar el modelo.
- **`ingesta/enriquecer_licitaciones_activas.py`** tampoco está en `run_pipeline.py` — correr manualmente para obtener predicciones sobre licitaciones abiertas.
- **Los ZIPs históricos no contienen licitaciones activas** — la API es la única fuente de licitaciones sin resolución.
- **`--paso csv/sii/api/ocds` no existen** — los pasos válidos son: `ingesta`, `transform`, `score`, `report`, `alertas`.
