# 🏦 Factoring Prospectos: Pipeline E2E de Inteligencia Comercial

Sistema automatizado de **prospección y scoring de leads para factoring**, diseñado para identificar oportunidades financieras de alta calidad mediante el análisis de datos de **Mercado Público (Licitaciones y Órdenes de Compra)** en Chile.

Este proyecto integra ingeniería de datos y finanzas, utilizando **Claude Code** para el desarrollo acelerado de lógica de negocio y automatización de procesos ETL.

---

## 🚀 Descripción del Proyecto

El sistema procesa el flujo masivo de adjudicaciones de organismos públicos para detectar proveedores que requieren liquidez inmediata. A través de un modelo de scoring avanzado, clasifica a los prospectos según su riesgo, historial de pagos y concentración de cartera.

### Características Principales
* **ETL Automatizado:** Ingesta diaria de datos de Mercado Público (Convenio Marco, Licitaciones, Suministros).
* **Scoring de 12 Variables:** Evaluación multidimensional que incluye ratios de OC/Licitación, estacionalidad y concentración por organismo.
* **Lógica Geográfica:** Predicción `mismo_region` para optimizar la gestión comercial territorial.
* **Alertas en Tiempo Real:** Loop de monitoreo (`alertas_loop.py`) para notificación inmediata de oportunidades detectadas.
* **Reportes BI:** Generación de dashboards en Excel y Power BI con análisis de plazos de pago.

---

## 🛠️ Stack Técnico

| Categoría | Herramientas |
|-----------|--------------|
| **Lenguaje** | Python 3.x |
| **IA & Agentes** | Claude Code (CLI), Anthropic API |
| **Data Science** | pandas, numpy, scikit-learn, statsmodels |
| **Automatización** | Batch scripting (`run_morning.bat`), Python Pipeline |
| **Reporting** | openpyxl (Excel), Power BI |

---

## 📂 Estructura del Repositorio

* `ingesta/`: Módulos de extracción de datos y conexión con APIs de Mercado Público.
* `transform/`: Limpieza de datos y feature engineering (Lógica E2, ventanas temporales).
* `models/`: Modelos de Machine Learning y lógica de scoring.
* `reports/`: Generación de outputs y plantillas para visualización comercial.
* `utils/`: Funciones auxiliares de configuración, auditoría y alertas.
* `tests/`: Pruebas unitarias para asegurar la integridad de la lógica financiera.

---

## 📊 Lógica de Scoring y Evolución

El proyecto implementa un sistema de decisión basado en datos históricos:
1.  **Feature Engineering:** Implementación de ratios críticos como `ratio_oc_licitacion` y lógicas de ventana temporal para capturar la vigencia real de la oportunidad.
2.  **Estrategias Comerciales:** El sistema permite elegir entre 3 estrategias distintas según el apetito de riesgo.
3.  **Auditoría:** Registro detallado de decisiones del modelo en `auditoria.md` para trazabilidad total.

---

## ⚙️ Configuración e Instalación

1.  **Clonar el repositorio:**
    ```bash
    git clone [https://github.com/pazssanti/factoring_prospectos.git](https://github.com/pazssanti/factoring_prospectos.git)
    ```
2.  **Instalar dependencias:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configurar entorno:**
    Editar `config.py` con las credenciales de API de Mercado Público y rutas locales.
4.  **Ejecutar Pipeline:**
    ```bash
    python run_pipeline.py
    ```

---

## 📫 Contacto

[![LinkedIn](https://img.shields.io/badge/LinkedIn-mpaz--santi-0A66C2?style=flat&logo=linkedin)](https://linkedin.com/in/mpaz-santi)
[![Email](https://img.shields.io/badge/Email-pazssanti@gmail.com-D14836?style=flat&logo=gmail)](mailto:pazssanti@gmail.com)
[![GitHub](https://img.shields.io/badge/GitHub-pazssanti-181717?style=flat&logo=github)](https://github.com/pazssanti)

---
*Código fuente de autoría propia - María Paz Santibáñez Silva (2025)*
