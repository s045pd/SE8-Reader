module.exports = {
  apps: [
    {
      name: "gunicorn",
      script:
        "gunicorn SE8.wsgi -b 127.0.0.1:8000 --capture-output",
      watch: false,
      autorestart: true
    },
    {
      name: "celery-worker",
      script: "sleep 20 && celery -A SE8 worker -l INFO -E --logfile /opt/server/vol/logs/celery-worker.log",
      autorestart: true
    },
    {
      name: "celery-beat",
      script: "sleep 20 && celery -A SE8 beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler --logfile /opt/server/vol/logs/celery-event.log",
      autorestart: true
    },

  ],
};
