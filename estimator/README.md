# Estimator CAG — Servicio de estimación de software con IA

 **[ Javier Cubelos Ordás ]** 

API **FastAPI** que genera estimaciones de proyectos de software a partir de parámetros tipados (descripción, tipo de proyecto, nivel de detalle, formato de salida), usando **Cache Augmented Generation (CAG)**: el contexto de referencia se inyecta como texto fijo en las plantillas de prompt (directorio `app/prompts/`), sin base de datos vectorial en esta versión.

## Requisitos previos

- **Docker** y **Docker Compose** (recomendado), o Python 3.11 con **uv**
- Clave de **OpenAI** y/o **Anthropic** (el router puede usar modelo principal y de respaldo)

## Inicio rápido con Docker

```bash
cd estimator
cp .env.example .env
# Editar .env: API keys, modelos, REDIS_URL si hace falta
docker compose up --build
```

La API queda en **[http://localhost:8000](http://localhost:8000)** y Redis en el servicio `redis` (puerto **6379** publicado en el host para inspección con `redis-cli`).

## Ejecución local (sin Docker)

```bash
cd estimator
uv sync
# .env con claves; para caché Redis en el mismo equipo: REDIS_URL=redis://localhost:6379
uv run uvicorn app.main:app --reload
```

## Uso de la API principal

`POST /api/v1/estimate` acepta un JSON con el contrato `EstimationRequest` (validación Pydantic). La respuesta incluye el texto de la estimación (`text`), la versión de plantillas usada (`prompt_version`), uso de tokens, coste estimado, acierto de caché y, si se pide, resultado de la validación estructural.

```bash
curl -s -X POST http://localhost:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "description": "The client wants a mobile app for restaurant reservations: registration, search with filters, real-time availability, push notifications, and an owner admin with basic analytics.",
    "project_type": "mobile_app",
    "detail_level": "medium",
    "output_format": "narrative",
    "evaluate": true
  }' | jq '{prompt_version, validation_score: .validation.score, text_preview: .text[0:120]}'
```

## Características

- **Plantillas Jinja2** versionadas (`app/prompts/estimation/v1/`) y renderizado centralizado en `app/prompts/loader.py`; el modelo recibe mensajes **system** y **user** separados.
- **LiteLLM** con modelo principal y **fallback**, tiempo de espera y reintentos configurables.
- **Caché exact-match** en Redis (clave derivada del system prompt, user message y parámetros de generación).
- **Streaming SSE**: `POST /api/v1/estimate/stream` para transcripciones largas con emisión token a token.
- **Ejemplos canónicos** en Python (`app/context/examples.py`) para el prompt ensamblado en código usado por el flujo de streaming.
- **Validación ligera** del markdown devuelto (tablas, totales, secciones) cuando `evaluate` es verdadero.

## Estructura del repositorio

```
estimator/
├── app/
│   ├── main.py              # FastAPI, CORS, estáticos, /health
│   ├── config.py            # Variables de entorno (Pydantic Settings)
│   ├── dependencies.py      # Caché y wrapper LLM (singletons)
│   ├── routers/
│   │   └── estimations.py   # POST /api/v1/estimate, POST .../estimate/stream
│   ├── services/
│   │   ├── llm_service.py   # Orquestación y dispatch al wrapper
│   │   ├── llm_wrapper.py   # LiteLLM, caché, costes
│   │   ├── cache.py         # Cliente Redis
│   │   └── evaluation.py    # Comprobaciones estructurales
│   ├── schemas/
│   │   └── estimation.py    # Request/response Pydantic
│   ├── prompts/
│   │   ├── loader.py          # Render system/user + versión
│   │   └── estimation/v1/     # system.j2, user.j2, examples.j2
│   ├── context/
│   │   └── examples.py        # Ejemplos CAG para build_system_prompt
│   └── fixtures/              # Transcripciones de ejemplo
├── streamlit_app.py         # Formulario → POST /api/v1/estimate
├── tests/
│   ├── prompts/
│   │   └── test_estimation_v1.py
│   └── ...
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Documentación interactiva

Con el servicio en marcha: [Swagger UI](http://localhost:8000/docs) y [ReDoc](http://localhost:8000/redoc).

## Streaming y demo HTML

```bash
curl -N -X POST http://localhost:8000/api/v1/estimate/stream \
  -H 'Content-Type: application/json' \
  -d '{"transcription": "We need a small CRM with auth, contacts and roles. MVP six weeks."}'
```

Demo estática: [http://localhost:8000/static/sse_demo.html](http://localhost:8000/static/sse_demo.html) (si existe el directorio `app/static`).

## Caché Redis

Misma petición repetida (mismo cuerpo y mismos prompts efectivos) puede servirse desde Redis y marcar `cache_hit: true` en la respuesta.

```bash
curl -s localhost:8000/api/v1/estimate -H 'Content-Type: application/json' \
  -d '{
    "description": "We need a small CRM with auth, contacts and roles for MVP in about six weeks.",
    "project_type": "web_saas",
    "detail_level": "summary",
    "output_format": "line_items"
  }'

docker compose exec redis redis-cli KEYS 'estimation:*'
```

Si ejecutas **solo uvicorn en el host**, usa `REDIS_URL=redis://localhost:6379` en `.env` y un Redis local; el hostname `redis` solo resuelve dentro de la red de Compose.

## Cliente Streamlit

Interfaz de formulario que envía `EstimationRequest` al endpoint JSON (no sustituye al streaming para demos largas).

```bash
cd estimator
uv sync
uv run streamlit run streamlit_app.py
```

Abrir **[http://localhost:8501](http://localhost:8501)**. La URL base de la API se configura con `ESTIMATOR_API_BASE_URL` (por defecto `http://localhost:8000`; en macOS a veces conviene `http://127.0.0.1:8000`).