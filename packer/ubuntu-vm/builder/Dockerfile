# Packer and QEMU for packer/ubuntu-vm (task image:build:ubuntu-vm): the build VM runs in this
# container, with /dev/kvm when the host has it
FROM ubuntu:24.04
ARG PACKER_VERSION=1.14.2
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       qemu-system-x86 qemu-utils xorriso openssh-client ca-certificates curl unzip \
    && curl -fsSL "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip" -o /tmp/packer.zip \
    && unzip -d /usr/local/bin /tmp/packer.zip packer \
    && rm -rf /tmp/packer.zip /var/lib/apt/lists/*
