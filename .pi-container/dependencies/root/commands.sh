#!/bin/bash
set -e

set_env_var() {
    local key="$1"
    local value="$2"

    # Construct the line formatted as KEY=\ VALUE\
    local line="${key}=${value}"

    # Check if the key already exists in the file
    if grep -q "^${key}=" /etc/environment; then
        # Replace the existing line
        sed -i "s|^${key}=.*|${line}|" /etc/environment
    else
        # Append the new line to the file
        echo "${line}" | tee -a /etc/environment > /dev/null
    fi
    export ${key}=${value}
}

set_env_var "PYTHON_VERSION" "3.14.6"
set_env_var "UV_SYSTEM_CERTS" "1"

echo "Installing apt packages"

apt-get update > /dev/null 2>&1 && apt-get install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libncurses5-dev \
    libncursesw5-dev \
    libreadline-dev \
    libsqlite3-dev \
    libgdbm-dev \
    libdb5.3-dev \
    libbz2-dev \
    libexpat1-dev \
    liblzma-dev \
    libffi-dev \
    uuid-dev > /dev/null 2>&1

wget -q https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz \
 && echo "74d0d71d0600e477651a077101d6e62d1e2e69b8e992ba18c993dd643b7ba222  Python-${PYTHON_VERSION}.tgz" | sha256sum -c - \
 && tar -xf Python-${PYTHON_VERSION}.tgz \
 && cd Python-${PYTHON_VERSION} \
 && echo "Building python version ${PYTHON_VERSION}" \
 && ./configure --enable-optimizations > /dev/null 2>&1 \
 && make -s -j $(nproc) > /dev/null 2>&1 \
 && make install > /dev/null 2>&1 \
 && ln -sf /usr/local/bin/python3.14 /usr/local/bin/python \
 && ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip \
 && ln -sf /usr/local/bin/idle3.14 /usr/local/bin/idle \
 && ln -sf /usr/local/bin/pydoc3.14 /usr/local/bin/pydoc \
 && ln -sf /usr/local/bin/python3.14-config /usr/local/bin/python-config

export PIP_ROOT_USER_ACTION=ignore

pip install --root-user-action=ignore --upgrade pip
pip install --root-user-action=ignore -r /dev/stdin <<EOF
uv==0.11.30 \
    --hash=sha256:cc28cb55c2b3c80a26ea374a172fec70b0561ada211e6fb23936ccea3ecb80b2 \
    --hash=sha256:ea5f0d4fe452dc3daf915c714504eb2f1e570f8ebac752abf51f9e6f58a1ff68 \
    --hash=sha256:7c41e83a2811c22e04ae50d0986932318ba82e6f9e29f0fca727d855df6bd959 \
    --hash=sha256:988133c7f44c6c64f6fef482483014995260cdb3a68270805256ddb9e6fed9e8 \
    --hash=sha256:7d9d922cfef27757156f1023eb057abab192e3e9f5436ba60eac57ffbc2b5c23 \
    --hash=sha256:a2aff328164d7e8fbcf6b82182cc16f7a729ec7edfce77b4d0c2908fca12bd63 \
    --hash=sha256:6a29031ff95150ea6156607394db8f79dfb06d5287f46ff07e3f60b9df76121c
EOF