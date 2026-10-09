FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY db.py schema.sql state_api.py radar.py radar_runs.py dashboard_server.py production_config.py production_app.py production_wsgi.py gunicorn.conf.py candidate-profile.json ./
COPY radar_console.py radar_privacy.py radar_gate.py radar_audit.py radar_recommendations.py radar_policy.py radar_policy.json RADAR_AGENT_INSTRUCTIONS.md ./
COPY radar_worker ./radar_worker
COPY migrations ./migrations
COPY dashboard ./dashboard
CMD ["gunicorn", "--config", "gunicorn.conf.py", "production_wsgi:application"]
