# Minimal deploy path for the web simulator (warehouse/web.py). See
# DEPLOYMENT.md for the full picture -- in particular, for an
# "excel"-data_source client, this image needs their workbook supplied at
# build time (COPY) or mount time (volume); it is deliberately not in git
# (see MIGRATION_LOG.md Phase 4). A "supabase"-data_source client needs
# nothing but $DATABASE_URL -- no local data file at all.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first: this layer only invalidates when requirements.txt
# changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HOST=0.0.0.0 \
    PORT=8000 \
    WAREHOUSE_CLIENT=sia

EXPOSE 8000

CMD ["python", "run_web.py"]
