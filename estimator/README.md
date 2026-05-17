# Estimator — Servicio IA de estimación de software

Servicio IA en FastAPI que estima proyectos de software a partir de un formulario tipado. Es la pieza Python del programa **Master en AI Engineering**: pensado para ser consumido por un backend de negocio (Rails, Streamlit u otro), no por un usuario final.

| Sesión | Qué aporta |
|--------|------------|
| **04** | `POST /api/v1/estimate` transaccional (JSON), salida estructurada (`EstimationResult`), guardrails, cache exacto + semántico (Redis). |
| **05** | Sesiones conversacionales, `project_metadata`, ventana deslizante, adjuntos PDF (Camino A multimodal), `POST /api/v1/sessions/.../estimate` (multipart). |

Rama de trabajo del ejercicio: **`pre-session-05`**.

---

## Requisitos previos

- [uv](https://docs.astral.sh/uv/) (gestor de dependencias Python)
- Docker y Docker Compose (opcional, recomendado para Redis)
- Al menos una de: `OPENAI_API_KEY` o `ANTHROPIC_API_KEY` en `.env` (para pruebas manuales con LLM real)

---

## Cómo levantar el servicio

### Opción A — Docker (recomendado)

Incluye API + Redis (cache del endpoint transaccional).

```bash
cd estimator
cp .env.example .env   # edita y añade tus API keys
docker compose up --build
```

- API: [http://localhost:8000](http://localhost:8000)
- Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health: [http://localhost:8000/health](http://localhost:8000/health)

### Opción B — Local con uv

Redis debe estar accesible en `REDIS_URL` (por defecto `redis://localhost:6379`) si quieres cache en el endpoint transaccional. Sin Redis, el servicio arranca pero el cache exacto puede fallar al conectar.

```bash
cd estimator
uv sync
cp .env.example .env   # si aún no existe
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Tras cambiar `.env`, reinicia uvicorn por completo (`get_settings()` usa `lru_cache`).

---

## Cómo probar (manual)

### 1. Comprobar que la API responde

```bash
curl -s http://localhost:8000/health | jq
```

### 2. Estimación transaccional (Session 4, JSON)

```bash
curl -s -X POST http://localhost:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "description": "A small B2B SaaS to manage employee equipment loans across teams.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table"
  }' | jq
```

Respuesta esperada: `result` (objeto con `phases`, `total_cost_eur`, …), `prompt_version`, `cached`.

### 3. Conversación multi-turno (Session 5, multipart)

```bash
# Crear sesión
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/v1/sessions | jq -r .session_id)
echo "session_id=$SESSION_ID"

# Turno 1
curl -s -X POST "http://localhost:8000/api/v1/sessions/${SESSION_ID}/estimate" \
  -F "transcript=We are building BookFlow, a SaaS for equipment loans. Team of 3 engineers." \
  -F "project_type=web_saas" \
  -F "detail_level=medium" \
  -F "output_format=phases_table" | jq '.project_metadata, .result.summary'

# Turno 2 (mencionar stack; project_metadata debería enriquecerse)
curl -s -X POST "http://localhost:8000/api/v1/sessions/${SESSION_ID}/estimate" \
  -F "transcript=We agreed on Rails API and React frontend with PostgreSQL." \
  -F "project_type=web_saas" \
  -F "detail_level=medium" \
  -F "output_format=phases_table" | jq '.project_metadata'

# Turno 3 con PDF (requiere OPENAI_API_KEY si PRIMARY_MODEL es OpenAI)
curl -s -X POST "http://localhost:8000/api/v1/sessions/${SESSION_ID}/estimate" \
  -F "transcript=Use the attached technical specification for sizing." \
  -F "project_type=web_saas" \
  -F "detail_level=medium" \
  -F "output_format=phases_table" \
  -F "attachments=@/ruta/a/spec.pdf;type=application/pdf" | jq '.result.confidence_pct'
```

Respuesta de sesión: `result`, `project_metadata`, `session_id`, `prompt_version`, `cached` (siempre `false` en sesiones).

### 4. Cliente Streamlit

En **otra terminal**, con la API ya levantada:

```bash
cd estimator
uv run streamlit run streamlit_app.py
```

Abre [http://localhost:8501](http://localhost:8501). Al cargar:

- Crea automáticamente una sesión (`POST /api/v1/sessions`).
- Muestra `project_metadata` en la barra lateral tras cada estimación.
- **Nueva conversación** resetea sesión y metadata.
- Sube PDFs opcionales (Camino A).

`ESTIMATOR_API_BASE_URL` en `.env` debe apuntar a la API (default `http://localhost:8000`).

---

## Endpoints

| Método | Ruta | Content-Type | Descripción |
|--------|------|--------------|-------------|
| `GET` | `/health` | — | Liveness |
| `POST` | `/api/v1/estimate` | `application/json` | Un shot; usa cache Redis |
| `POST` | `/api/v1/sessions` | — | Crea sesión → `{ "session_id": "..." }` |
| `POST` | `/api/v1/sessions/{session_id}/estimate` | `multipart/form-data` | Un turno conversacional |

Campos multipart del turno de sesión: `transcript`, `project_type`, `detail_level`, `output_format`, `attachments` (opcional, PDF).

---

## Sesión 05 — decisiones de diseño

### Adjuntos: Camino A (multimodal directo)

Los PDF se suben a la **Files API** del proveedor de `PRIMARY_MODEL`:

- **OpenAI:** `files.create` + bloques `file` en el mensaje user.
- **Anthropic:** `beta.files.upload` + bloques `document` (header `anthropic-beta: files-api-2025-04-14`).

**Por qué Camino A:** menos código, el modelo ve diagramas/tablas, `file_id` se reutiliza en turnos posteriores. **Trade-off:** lock-in multimodal; solo **PDF** (Word no soportado en esta fase).

### Memoria: `project_metadata` vía LLM extractor

Tras cada estimación, una segunda llamada (`gpt-4o-mini` + Instructor) actualiza `ProjectMetadata` (`app/prompts/metadata/v1/extractor.j2`).

**Por qué extractor y no heurística:** conversación libre y posiblemente bilingüe; regex se rompe con variaciones naturales.

### Historial vs memoria

- **`ConversationHistory`:** ventana deslizante (`MAX_CONVERSATION_TURNS`, default 6 pares). El system prompt se regenera cada turno; no vive en el historial.
- **`ProjectMetadata`:** hechos que sobreviven al truncado del historial.
- **Store:** `dict` en memoria del proceso; se pierde al reiniciar uvicorn.

---

## Cómo ejecutar los tests

Los tests **no llaman a APIs externas** salvo que ejecutes pruebas manuales con keys reales. Usan `fakeredis`, mocks de LiteLLM y `dependency_overrides` de FastAPI.

```bash
cd estimator
uv sync --group dev    # pytest, ruff, fakeredis
uv run pytest          # suite completa (~1 s)
uv run pytest -v       # verbose
uv run pytest tests/test_sessions.py -v   # solo sesión 05
uv run ruff check app tests             # linter (opcional)
```

### Catálogo de tests

| Archivo | Qué cubre |
|---------|-----------|
| `test_health.py` | `GET /health` → 200 healthy |
| `test_schemas.py` | Validación `EstimationRequest` / `EstimationResult` (enums, longitudes, suma de fases, out-of-scope) |
| `test_prompts.py` | Render Jinja2: bloques condicionales, ejemplos, `StrictUndefined` |
| `test_estimate_endpoint.py` | `POST /api/v1/estimate` con `EstimationService` fake (200/422, contrato JSON) |
| `test_guardrails_input.py` | Moderación, injection, PII, orden de capas |
| `test_guardrails_output.py` | `enforce_scope_response` (filtro baja confianza) |
| `test_cache.py` | Cache exacto Redis: claves, TTL, roundtrip |
| `test_cache_semantic.py` | Cache semántico: buckets, umbral, log-only |
| `test_llm_wrapper.py` | Wrapper LiteLLM: coste, cache, override, thinking budget |
| `test_sessions.py` | Sesiones: ventana, metadata en 2 turnos, PDF mock, 8 POSTs al LLM |

Cada función `test_*` incluye un docstring de una línea que describe qué comprueba.

---

## Estructura del proyecto

```
estimator/
├── app/
│   ├── main.py                 # FastAPI app, CORS, routers
│   ├── config.py               # Settings desde .env (Pydantic Settings)
│   ├── dependencies.py         # Singletons: cache, LLM, servicios, session store
│   ├── routers/
│   │   ├── estimations.py      # POST /api/v1/estimate (JSON)
│   │   └── sessions.py         # POST /api/v1/sessions, .../estimate (multipart)
│   ├── schemas/
│   │   ├── estimation.py       # EstimationRequest, EstimationResult, enums
│   │   └── session.py          # CreateSessionResponse, SessionEstimateResponse
│   ├── services/
│   │   ├── estimation.py       # Pipeline transaccional (guardrails + cache + LLM)
│   │   ├── session_estimation.py  # Pipeline multi-turno por sesión
│   │   ├── sessions.py         # SessionStore, ConversationHistory, ProjectMetadata
│   │   ├── attachments.py      # Files API PDF (Camino A)
│   │   ├── llm_wrapper.py      # LiteLLM + Instructor (structured + messages)
│   │   └── cache.py            # Cache exacto Redis
│   ├── cache/
│   │   └── semantic.py         # Cache semántico (redisvl + embeddings)
│   ├── guardrails/
│   │   ├── input.py            # Moderación, injection, PII
│   │   └── output.py           # Filtro out-of-scope
│   └── prompts/
│       ├── loader.py           # Render Jinja2 (estimate + session + extractor)
│       ├── estimation/v1/
│       │   ├── system.j2       # Rol, reglas, project_metadata, ejemplos
│       │   ├── user.j2         # Flujo transaccional
│       │   ├── session_user.j2 # Flujo conversacional (<transcript>)
│       │   └── examples.j2
│       └── metadata/v1/
│           └── extractor.j2    # Prompt extractor ProjectMetadata
├── tests/                      # Ver catálogo arriba; conftest.py → client fixture
├── streamlit_app.py            # Cliente HTTP multi-turno
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── docker-compose.yml          # API + redis-stack
├── .env.example
└── README.md
```

### Flujos de datos (resumen)

```
Transaccional:  Cliente → POST /estimate (JSON) → EstimationService → [guardrails → cache → LLM] → EstimationResponse

Conversacional: Cliente → POST /sessions → session_id
                Cliente → POST /sessions/{id}/estimate (multipart) → SessionEstimationService
                  → [guardrails → upload PDF → messages + metadata en prompt → LLM estimación
                     → extractor metadata → append historial] → SessionEstimateResponse
```

---

## Variables de entorno

| Variable | Default | Uso |
|----------|---------|-----|
| `OPENAI_API_KEY` | — | Moderación, embeddings, modelos OpenAI, Files API PDF |
| `ANTHROPIC_API_KEY` | — | Fallback Router, Files API si primary es Claude |
| `PRIMARY_MODEL` | `gpt-4o-mini` | Estimación + subida multimodal en sesiones |
| `FALLBACK_MODEL` | `claude-haiku-4-5-20251001` | Fallback solo en endpoint transaccional |
| `MAX_CONVERSATION_TURNS` | `6` | Pares user/assistant en ventana deslizante |
| `REDIS_URL` | `redis://localhost:6379` | Cache exacto (y semántico si Redis Stack disponible) |
| `CACHE_TTL` | `86400` | TTL cache exacto (segundos) |
| `SEMANTIC_CACHE_THRESHOLD` | `0.85` | Umbral similitud coseno |
| `SEMANTIC_CACHE_LOG_ONLY` | `false` | Si `true`, solo loguea hits semánticos |
| `ESTIMATOR_API_BASE_URL` | `http://localhost:8000` | URL base para Streamlit |
| `APP_ENV` | `development` | `development` → logs legibles; `production` → JSON |

Copia `.env.example` a `.env` y rellena al menos una API key.

---

## Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---------|----------------|-----------|
| `502` en sesión con PDF | Files API / modelo no multimodal | Usa `PRIMARY_MODEL=gpt-4o-mini` y `OPENAI_API_KEY` |
| `400` en adjunto | No es PDF o content-type inválido | Solo `.pdf` en Camino A |
| `404` en estimate de sesión | `session_id` incorrecto o servidor reiniciado | Crear nueva sesión (`POST /sessions`) |
| Cache / Redis error al arrancar | Redis no levantado | `docker compose up` o desactiva uso de cache en local |
| Streamlit no conecta | API en otro puerto | Ajusta `ESTIMATOR_API_BASE_URL` en `.env` |
| Metadata vacía tras turnos | LLM real no ejecutado o extractor falló | Revisa logs structlog; comprueba keys |

---

> Proyecto del **Master en AI Engineering**.
