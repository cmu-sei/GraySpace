FROM python:3.14.10-slim

# Install System Dependencies with BuildKit cache mount
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl=8.14.* \
    unzip=6.* \
    openssh-client=1:10.* \
    rsync=3.* \
    git=1:2.47.* \
    sshpass=1.* \
    locales=2.* \
    sudo=1.* \
    sshuttle=1.3.* \
    procps=2:4.* \
    shellcheck=0.* \
    gh=2.* \
    rsyslog

# Set the locale to UTF-8
RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales && \
    update-locale LANG=en_US.UTF-8

ENV LANG=en_US.UTF-8

# Custom Cert Support
COPY .devcontainer/certs /usr/local/share/ca-certificates/custom/
RUN find /usr/local/share/ca-certificates/custom -type f ! -name '*.crt' -delete \
    && if [ -n "$(find /usr/local/share/ca-certificates/custom -type f -name '*.crt' -print -quit)" ]; then \
    update-ca-certificates; \
    fi

# Install Terraform, Packer, and Task
ARG TARGETARCH
ENV TERRAFORM_VERSION=1.13.4
ENV PACKER_VERSION=1.14.2
ENV TASK_VERSION=3.49.1
RUN --mount=type=cache,target=/tmp/hashicorp-dl \
    # terraform
    [ -f /tmp/hashicorp-dl/terraform_${TERRAFORM_VERSION}_${TARGETARCH}.zip ] || \
    curl -LfSs "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
      -o /tmp/hashicorp-dl/terraform_${TERRAFORM_VERSION}_${TARGETARCH}.zip \
    && unzip /tmp/hashicorp-dl/terraform_${TERRAFORM_VERSION}_${TARGETARCH}.zip terraform -d /usr/local/bin \
    # packer
    && { [ -f /tmp/hashicorp-dl/packer_${PACKER_VERSION}_${TARGETARCH}.zip ] || \
    curl -LfSs "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_${TARGETARCH}.zip" \
      -o /tmp/hashicorp-dl/packer_${PACKER_VERSION}_${TARGETARCH}.zip; } \
    && unzip /tmp/hashicorp-dl/packer_${PACKER_VERSION}_${TARGETARCH}.zip packer -d /usr/local/bin \
    # task
    && curl -LfSs "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_linux_${TARGETARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin task

# Create vscode user early with passwordless sudo; keep proxy vars (from proxy.env) under sudo
RUN useradd -m -s /bin/bash vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && echo 'Defaults env_keep += "http_proxy https_proxy ftp_proxy no_proxy HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY"' >> /etc/sudoers.d/vscode

# Copy requirements.txt first for better layer caching
COPY requirements.txt /tmp/requirements.txt

# Create Python venv and install dependencies as vscode user
USER vscode
WORKDIR /home/vscode
RUN --mount=type=cache,target=/home/vscode/.cache/pip,uid=1000,gid=1000 \
    python3 -m venv /home/vscode/.venv && \
    /home/vscode/.venv/bin/pip install --upgrade pip && \
    /home/vscode/.venv/bin/pip install -r /tmp/requirements.txt

# Install Claude Code (before COPY steps that bust cache frequently)
RUN curl -fsSL https://claude.ai/install.sh | bash

# Pre-initialize Terraform providers to cache them in the image
USER root
RUN mkdir -p /tmp/terraform-cache && cd /tmp/terraform-cache && \
    printf 'terraform {\n  required_providers {\n    proxmox = {\n      source = "bpg/proxmox"\n      version = "0.114.0"\n    }\n  }\n}\n' > main.tf && \
    terraform init && \
    mkdir -p /home/vscode/.terraform.d/plugin-cache && \
    cp -r .terraform/providers/* /home/vscode/.terraform.d/plugin-cache/ 2>/dev/null || true && \
    chown -R vscode:vscode /home/vscode/.terraform.d && \
    rm -rf /tmp/terraform-cache

# Configure Terraform to use plugin cache
USER vscode
RUN printf 'plugin_cache_dir = "$HOME/.terraform.d/plugin-cache"\n' > /home/vscode/.terraformrc

WORKDIR /workspaces