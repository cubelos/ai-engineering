# Estimator — Servicio IA de estimación de software

Servicio IA en FastAPI que estima proyectos de software a partir de un formulario tipado. Es la pieza Python del programa **Master en AI Engineering**: un endpoint pensado para ser consumido por un backend de negocio (Rails, Streamlit u otro), no por un usuario final.

A partir de la **Sesión 04** el contrato es deliberadamente estrecho:
- entrada tipada (`description` + tres enums),
- salida en texto libre,
- prompt fuera del código en templates Jinja2 versionados (`app/prompts/<use_case>/<version>/`).

La inteligencia adicional (output estructurado, guardrails, cache semántico) se construye encima de esta base en directo.

## Cómo levantar

### Con Docker (recomendado)

```bash
cd estimator
cp .env.example .env  # añade al menos OPENAI_API_KEY o ANTHROPIC_API_KEY
docker compose up --build
```

El servicio queda en `http://localhost:8000` (Swagger en `/docs`, health en `/health`). Redis arranca como servicio vecino para el cache exact-match del wrapper.

### Sin Docker

```bash
cd estimator
uv sync
uv run uvicorn app.main:app --reload
```

### Probar el endpoint

```bash
curl -X POST http://localhost:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "description": "A small B2B SaaS to manage employee equipment loans across teams. Role-based access, audit trail, weekly digest.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table"
  }'
```

Respuesta:

```json
{
  "text": "| phase | duration_weeks | cost_eur | confidence_pct | …",
  "prompt_version": "v1"
}
```

### Cliente Streamlit

El cliente Streamlit es un formulario que construye el JSON y muestra el `text` recibido. Corre fuera de Docker y consume la API por HTTP:

```bash
cd estimator
uv run streamlit run streamlit_app.py
# Abrir http://localhost:8501
```

La URL del servicio se lee de `ESTIMATOR_API_BASE_URL` (default `http://localhost:8000`).

## Cómo testar

```bash
cd estimator
uv run pytest
```

La batería corre en milisegundos sin tocar APIs externas (salvo que ejecutes el stress runner con `--http` contra un servicio vivo). Cubre:

- `tests/test_schemas.py` — validaciones del `EstimationRequest`.
- `tests/test_prompts.py` / `tests/test_prompts_v3.py` — render de templates Jinja2 versionados.
- `tests/test_estimate_endpoint.py` — endpoint transaccional con LLM mockeado.
- `tests/test_llm_wrapper.py` y `tests/test_cache.py` — wrapper, cost tracking y cache exact-match.
- `tests/test_sessions_*.py` — flujo conversacional, adjuntos, ventana, metadata, ACB.
- `tests/test_evals_*.py` — golden dataset (16 casos) y runner de evals.
- `tests/test_turn_observed.py` — evento unificado `turn_observed` y GET enriquecido.
- `tests/test_stress_metrics.py` — métricas de presupuesto y memory drift del stress framework.

## Estructura del proyecto

```
estimator/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── dependencies.py
│   ├── observability/
│   │   └── turn_accumulator.py      # agrega tokens/coste/latencia por turno CAG
│   ├── routers/
│   │   ├── estimations.py           # POST /api/v1/estimate (transaccional)
│   │   └── sessions.py              # POST/GET /sessions, estimate conversacional
│   ├── schemas/
│   │   ├── estimation.py
│   │   └── observation.py           # TurnObservation (turn_observed)
│   ├── sessions/                    # memoria conversacional + compresión
│   ├── prompts/                     # templates Jinja2 versionados (v1, v2, v3, …)
│   └── services/
│       ├── llm_wrapper.py           # LiteLLM + Instructor + cost tracking
│       ├── estimation.py            # pipeline transaccional y conversacional
│       └── cache.py
├── evals/
│   ├── golden_dataset.json          # 16 casos golden
│   ├── metrics.py                   # SchemaAdherence, CostBounds, ContentRecall
│   ├── run.py                       # uv run python -m evals.run
│   └── stress/                      # baseline CAG (Sesión 6) — ver abajo
│       ├── scenarios.py             # perfiles growing / pivot / contradiction
│       ├── metrics.py               # LatencyBudget, CostBudget, MemoryDrift
│       ├── run.py
│       ├── results.csv              # entregable: filas por turno
│       ├── REPORT.md                # entregable: curvas + lectura
│       └── fixtures/build_pdfs.py   # PDFs sintéticos (regenerables)
├── tests/
├── streamlit_app.py
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

### Versionado de prompts

La estructura `app/prompts/<use_case>/<version>/` no es opcional: `v1/` ya existe desde el primer día porque versionar un prompt es la forma más barata de habilitar A/B testing y rollback en producción. Cuando una iteración del prompt se cocina, se crea `v2/` al lado y `render_estimation_prompt(request, version="v2")` lo recoge sin tocar router ni schemas.

Lo que vive **fuera** del template (en código): el contrato (`EstimationRequest`), el switch de versión y el wrapper. Todo lo demás (rol del modelo, reglas, ejemplos, formatos de salida, niveles de detalle) vive dentro del `.j2`. Si para cambiar el comportamiento del modelo hay que tocar Python, la separación está rota.

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `OPENAI_API_KEY` | — | Requerido al menos uno de los dos |
| `ANTHROPIC_API_KEY` | — | Requerido al menos uno de los dos |
| `PRIMARY_MODEL` | `gpt-4o-mini` | Modelo principal del Router |
| `FALLBACK_MODEL` | `claude-haiku-4-5-20251001` | Se usa si el primario falla |
| `REDIS_URL` | `redis://localhost:6379` | Cache exact-match |
| `CACHE_TTL` | `86400` | Segundos |
| `APP_ENV` | `development` | Controla el renderer de structlog |
| `ESTIMATOR_API_BASE_URL` | `http://localhost:8000` | Lo lee el cliente Streamlit |

`get_settings()` es un singleton cacheado con `lru_cache`: cualquier cambio en `.env` requiere reiniciar uvicorn (no basta con `--reload`).

---

## Sesión 5 — Memoria conversacional y adjuntos

A partir de la Sesión 05 el estimator deja de ser puramente transaccional y soporta **sesiones conversacionales**: el cliente puede refinar el alcance del proyecto a lo largo de varios turnos, subir documentos (PDF/Word) y el sistema recuerda el proyecto en curso entre llamadas. El endpoint `POST /api/v1/estimate` original se mantiene intacto para compatibilidad y para la demo transaccional.

### Endpoints nuevos

```
POST /sessions                              → 201 {"session_id": "<uuid>"}
GET  /sessions/{session_id}                 → 200 {session_id, message_count, metadata,
                                               summary, anchors_text, last_turn_observation, …}
POST /sessions/{session_id}/estimate        → 200 EstimationResponse
   (multipart/form-data: transcript, project_type, detail_level, output_format, attachments[])
POST /sessions/{session_id}/estimate-acb    → 200 ACBResponse (variante Actor-Critic-Boss)
```

Ejemplo end-to-end con httpie:

```bash
http POST :8000/sessions
# {"session_id": "abc-123"}

http -f POST :8000/sessions/abc-123/estimate \
  transcript="Queremos estimar un CRM llamado Nimbus en React + Postgres para el equipo de ventas." \
  project_type=web_saas detail_level=medium output_format=phases_table \
  attachments@spec.pdf

http GET :8000/sessions/abc-123
# Inspecciona el ProjectMetadata acumulado y el tamaño del historial.
```

Y un segundo turno reutilizando el mismo `session_id` sin repetir el contexto:

```bash
http -f POST :8000/sessions/abc-123/estimate \
  transcript="Añade un módulo de facturación con Stripe." \
  project_type=web_saas detail_level=medium output_format=phases_table
```

La respuesta del segundo turno integra Nimbus + React + Postgres + facturación porque el `<project_metadata>` se inyecta en el system prompt y el historial reciente viaja en el array `messages`.

### Decisiones de diseño

1. **Camino B para los adjuntos.** Extraemos el texto del PDF/Word **dentro del servicio IA** con `pypdf` y `python-docx`, lo recortamos a `MAX_ATTACHMENT_CHARS` y lo concatenamos al transcript con fences explícitos (`--- attachment: spec.pdf ---`). La alternativa (Camino A: subir el binario a la Files API de OpenAI o Anthropic) habría sido más corta de implementar pero acopla el wrapper a un proveedor multimodal concreto. Camino B mantiene `complete_structured_chat` agnóstico de proveedor (texto en, texto fuera vía LiteLLM Router + Instructor) y prepara el terreno para el chunking real de RAG en el módulo 3. La extracción es robusta a páginas corruptas (fallos por página se loguean y se ignoran) y a archivos vacíos.

2. **`project_metadata` con extractor LLM, no heurística.** Tras cada respuesta del estimador, una **segunda llamada** al LLM (modelo barato configurable vía `METADATA_EXTRACTOR_MODEL`, por defecto `gpt-4o-mini`) lee el último turno y devuelve un `ProjectMetadata` parcial vía Instructor. Lo fusionamos con el previo: campos escalares sobrescriben si vienen no-nulos, la lista de tecnologías se une case-insensitively. Se eligió el extractor LLM frente a una heurística regex porque el coste de una llamada con prompt corto es marginal y la robustez frente a paráfrasis del usuario es mucho mejor — y porque el curso enseña precisamente cómo construir estos pasos con LLMs. Si la llamada falla, se loguea y se conserva la metadata previa: la conversación no se cae por una extracción rota.

3. **Memoria en proceso, no Redis ni Postgres.** El `SessionStore` es un `dict` en memoria del worker FastAPI. La volatilidad (estado perdido al reiniciar el contenedor) es **intencional** para esta fase y está documentada en el docstring del store. La persistencia entre reinicios entra en el directo cuando hablemos de compresión de memoria con anclas.

4. **Cachés desactivadas en el path conversacional.** Cada turno depende del historial + metadata + adjuntos: dos transcripciones idénticas en sesiones distintas **no** son la misma llamada. El método nuevo `EstimationService.estimate_conversational` por tanto no consulta ni el cache exact-match ni el semántico, y `EstimationResponse.cached` siempre es `false` en este path. El endpoint transaccional original `POST /api/v1/estimate` sigue usando las dos cachés sin cambios.

5. **Ventana deslizante + compresión.** Con `MAX_CONVERSATION_TURNS=6` por defecto, los pares más antiguos se promueven a **anclas** (heurística o LLM) o se absorben en un **summary acumulativo** antes de salir del contexto. El system prompt se regenera cada turno desde `ProjectMetadata` + tier (`v3`).

6. **Tier dinámico y ACB.** `resolve_tier()` elige audiencia (executive / pm / developer / default) por reglas sobre transcript + metadata. La variante `/estimate-acb` añade Actor-Critic-Boss con traza de iteraciones en la respuesta.

### Variables de entorno nuevas (Sesión 5)

| Variable | Default | Notas |
|---|---|---|
| `MAX_CONVERSATION_TURNS` | `6` | Pares user+assistant que mantiene la ventana. |
| `MAX_ATTACHMENT_CHARS` | `60000` | Corte por archivo extraído. Trunca, no rechaza. |
| `METADATA_EXTRACTOR_MODEL` | `gpt-4o-mini` | Modelo de la segunda llamada por turno. |
| `COMPRESSION_MODEL` | `gpt-4o-mini` | Summarizer de historial evictado. |
| `ANCHOR_DETECTION_MODE` | `heuristic` | `heuristic` o `llm`. |
| `CONVERSATIONAL_PROMPT_VERSION` | `v3` | Template conversacional (+ bloque `<audience>`). |
| `CRITIC_MODEL` | `gpt-4o-mini` | Auditor del patrón ACB. |
| `BOSS_MAX_ITERATIONS` | `3` | Iteraciones máximas Actor-Critic-Boss. |

### Tests conversacionales

```bash
uv run pytest tests/test_sessions_metadata.py tests/test_sessions_attachments.py \
  tests/test_sessions_window.py tests/test_compression_policy.py -v
```

Integración con `TestClient` + `FakeLLMWrapper` + `SessionStore` aislado por test.

### Cliente Rails

El cliente Rails (`estimator-web/`) consume el flujo conversacional vía `ChatSessionsController` (rutas `/chat_sessions`). El endpoint transaccional histórico se mantiene operativo.

---

## Sesión 6 — Baseline cuantitativo del CAG (stress test)

Antes de introducir RAG hay que **medir** el CAG conversacional: latencia, coste acumulado y pérdida de memoria bajo conversaciones largas, adjuntos grandes y repetición. El deliverable es un reporte con números, no código de producción nuevo.

### Observabilidad por turno: `turn_observed`

Cada `POST /sessions/{id}/estimate` emite un único evento structlog `turn_observed` con 13 campos agregados (tokens, coste y latencia **suman** estimación + extractor de metadata + summarizer/anchors del turno):

| Campo | Descripción |
|---|---|
| `turn_index` | 1-based dentro de la sesión |
| `session_id` | UUID de la sesión |
| `enriched_transcript_chars` | transcript + texto de adjuntos |
| `attachments_total_chars` | solo la porción extraída de adjuntos |
| `messages_in_window` | `len(history.messages)` post-compresión |
| `anchors_count` / `summary_chars` | estado de compresión |
| `tokens_in` / `tokens_out` / `cost_usd` / `latency_ms` | agregados del turno |
| `cache_hit_kind` | `"none"` en path conversacional |
| `last_resolved_tier` | tier resuelto en ese turno |

El mismo payload queda en `session.last_turn_observation` y es accesible vía `GET /sessions/{id}` (junto con `summary`, `anchors_text` y `metadata` para métricas de drift).

### Golden evals (regresión)

```bash
uv run python -m evals.run --mode actor
uv run python -m evals.run --mode actor --http http://localhost:8000
```

16 casos en `evals/golden_dataset.json`; tres métricas binarias deterministas: `schema_adherence`, `cost_bounds`, `content_recall`.

### Stress runner (baseline CAG)

Genera PDFs sintéticos si faltan, ejecuta tres escenarios multi-turno (`growing`, `pivot`, `contradiction`) cruzados con tamaños de adjunto (0, 5, 20, 50, 100 KB) y escribe CSV + REPORT:

```bash
cd estimator

# Smoke test in-process (sin API keys, FakeLLMWrapper)
uv run python -m evals.stress.run \
  --scenarios growing --attachment-sizes 0 --repeats 1 --max-turns 3

# Baseline real contra servicio vivo (requiere OPENAI_API_KEY en .env)
docker compose up --build   # o: uv run uvicorn app.main:app --reload
uv run python -m evals.stress.run \
  --http http://localhost:8000 \
  --scenarios growing,pivot,contradiction \
  --attachment-sizes 0,5,20,50,100 \
  --repeats 3 \
  --max-turns 20 \
  --output evals/stress/results.csv \
  --report evals/stress/REPORT.md
```

Regenerar reporte desde CSV existente (p. ej. tras corregir pricing):

```bash
uv run python -m evals.stress.run \
  --from-csv evals/stress/results.csv \
  --output evals/stress/results.csv \
  --report evals/stress/REPORT.md
```

**Presupuestos SLA en métricas:** latencia ≤ 4000 ms, coste ≤ 0.05 USD/turno.

**Entregables del ejercicio** (van en el repo):

| Archivo | Contenido |
|---|---|
| `evals/stress/REPORT.md` | Tabla resumen, tres curvas (tablas), dos párrafos de lectura |
| `evals/stress/results.csv` | Una fila por turno medido + columnas de métricas |

Los PDFs de `evals/stress/fixtures/*.pdf` están en `.gitignore`; se regeneran con:

```bash
uv run python -m evals.stress.fixtures.build_pdfs
```

### Tests del stress framework

```bash
uv run pytest tests/test_stress_metrics.py tests/test_turn_observed.py -v
```

---

> Este proyecto forma parte del **Master en AI Engineering**. Sesión 04: output estructurado, guardrails, cache semántico. Sesión 05: memoria conversacional, compresión (anclas + summary), tier, ACB. Sesión 06: instrumentación `turn_observed` + stress baseline CAG (`evals/stress/`) como punto de comparación previo a RAG.
