from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "publicar_ad_mlb",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.tasks.ai_tasks",
        "app.workers.tasks.batch_tasks",
        "app.workers.tasks.category_tasks",
        "app.workers.tasks.image_tasks",
        "app.workers.tasks.publish_tasks",
        "app.workers.tasks.raw_photo_tasks",
        "app.workers.tasks.ai_engine_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    task_routes={
        "app.workers.tasks.ai_tasks.*": {"queue": "ai"},
        "app.workers.tasks.batch_tasks.*": {"queue": "default"},
        "app.workers.tasks.image_tasks.*": {"queue": "images"},
        "app.workers.tasks.publish_tasks.*": {"queue": "publish"},
        "app.workers.tasks.category_tasks.*": {"queue": "default"},
        "app.workers.tasks.raw_photo_tasks.*": {"queue": "default"},
        "app.workers.tasks.ai_engine_tasks.*": {"queue": "default"},
    },
    # Tarefas periodicas (o beat era vazio ate 2026-09-10): retomam listings
    # em standby — `pending_raw_photos` quando as fotos aparecem no bucket,
    # `pending_ai_engine` quando o motor de IA (credito OpenAI) volta.
    beat_schedule={
        "check-pending-raw-photos": {
            "task": "app.workers.tasks.raw_photo_tasks.check_pending_raw_photos",
            "schedule": 15 * 60,
        },
        "check-pending-ai-engine": {
            "task": "app.workers.tasks.ai_engine_tasks.check_pending_ai_engine",
            "schedule": 15 * 60,
        },
    },
)
