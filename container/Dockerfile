# GraySpace in one container: the Terrain API, the tools its jobs run (Terraform, Ansible, task) and
# the UI behind nginx. See docs/install.md ("Run GraySpace in a container").
# Build from the repository root:  docker compose -f container/compose.yaml build

# ── UI ─────────────────────────────────────────────────────────────────────────────────────────
# Node 24 (npm 11), as in the devcontainer that maintains package-lock.json; npm 10 rejects the lockfile.
FROM node:24-bookworm-slim AS ui
COPY .devcontainer/certs /usr/local/share/ca-certificates/custom/
RUN find /usr/local/share/ca-certificates/custom -type f ! -name '*.crt' -delete \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
WORKDIR /ui
# Swagger UI for /api/docs (routers/api_docs.py), served from the image rather than a CDN
ARG SWAGGER_UI_VERSION=5.34.0
RUN npm pack --silent "swagger-ui-dist@${SWAGGER_UI_VERSION}" && mkdir /swagger-ui \
    && tar -xzf swagger-ui-dist-*.tgz -C /swagger-ui --strip-components=1 && rm swagger-ui-dist-*.tgz
COPY grayspace-web/frontend/package.json grayspace-web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY grayspace-web/frontend/ ./
RUN npm run build

# ── runtime ────────────────────────────────────────────────────────────────────────────────────
# Python 3.13: the backend's pins (pydantic 2.9) have no build for 3.14.
FROM python:3.13-slim-bookworm
ARG TARGETARCH=amd64
ARG TERRAFORM_VERSION=1.13.4
ARG TASK_VERSION=3.49.1

COPY .devcontainer/certs /usr/local/share/ca-certificates/custom/
RUN find /usr/local/share/ca-certificates/custom -type f ! -name '*.crt' -delete \
    && apt-get update && apt-get install -y --no-install-recommends \
       ca-certificates curl unzip nginx openssh-client sshpass rsync \
    && update-ca-certificates \
    && curl -LfsS "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" -o /tmp/terraform.zip \
    && unzip /tmp/terraform.zip terraform -d /usr/local/bin && rm /tmp/terraform.zip \
    && curl -LfsS "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_linux_${TARGETARCH}.tar.gz" | tar -xz -C /usr/local/bin task \
    && apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Two environments: the root requirements (Ansible) and the backend's pins conflict (paramiko).
COPY requirements.txt /tmp/ops-requirements.txt
COPY grayspace-web/backend/requirements.txt /tmp/web-requirements.txt
RUN python -m venv /opt/venv-ops \
    && grep -E '^(ansible|netaddr)[<>=]' /tmp/ops-requirements.txt > /tmp/ops.txt \
    && /opt/venv-ops/bin/pip install -r /tmp/ops.txt \
    && python -m venv /opt/venv-web && /opt/venv-web/bin/pip install -r /tmp/web-requirements.txt \
    && rm /tmp/*.txt
# task slice:* runs the backend's code with the backend's Python (Taskfile BACKEND_PYTHON).
ENV PATH=/opt/venv-ops/bin:$PATH BACKEND_PYTHON=/opt/venv-web/bin/python \
    SWAGGER_UI_PATH=/opt/swagger-ui \
    # Where the control plane keeps deployments and accounts; also for task user:create in docker compose exec
    GRAYSPACE_DEPLOYMENTS_PATH=/data/deployments

# The code, read-only. The entrypoint copies it into the working tree on /data, around the state.
COPY . /opt/grayspace/
COPY --from=ui /ui/build /opt/grayspace-ui/
COPY --from=ui /swagger-ui /opt/swagger-ui/
COPY container/nginx.conf /etc/nginx/conf.d/grayspace.conf
COPY container/entrypoint.sh /usr/local/bin/grayspace-entrypoint

VOLUME /data
EXPOSE 80
WORKDIR /data/work
ENTRYPOINT ["grayspace-entrypoint"]
