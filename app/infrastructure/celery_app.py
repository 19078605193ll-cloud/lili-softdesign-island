from celery import Celery
from app.config import get_settings

celery_app = Celery(
    "island",
    broker=get_settings().celery_broker_url,
    include=["app.infrastructure.worker"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=240,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 360},
)
