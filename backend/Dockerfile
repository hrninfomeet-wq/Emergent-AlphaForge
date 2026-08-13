# Pinned by DIGEST, not by tag. `python:3.11-slim` floats: a routine rebuild on
# 2026-08-12 silently swapped the interpreter underneath us (CPython 3.11.15
# built Jun 24 2026 -> Aug 5 2026) with no repo change, which cost a day of
# bisecting during the optimizer worker-pool incident. It turned out not to be
# the cause, but an interpreter that changes without a commit makes every future
# incident harder to reason about.
#
# This digest resolves to:
#   Python 3.11.15 (main, Aug  5 2026, 01:10:23) [GCC 14.2.0]
#   Debian GNU/Linux 13 (trixie), glibc 2.41-12+deb13u3
#
# Trade-off: base-image security updates now arrive only when we ask for them.
# To update deliberately (and record it in a commit):
#   docker pull python:3.11-slim
#   docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}'
# then paste the new digest here and rebuild.
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install build deps for numpy/scipy/optuna
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8001/api/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
