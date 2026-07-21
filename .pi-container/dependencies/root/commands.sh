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

apt-get update && apt-get install -y \
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
    uuid-dev \
    wget \
    curl

wget https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz \
 && tar -xf Python-${PYTHON_VERSION}.tgz \
 && cd Python-${PYTHON_VERSION} \
 && ./configure --enable-optimizations \
 && make -j $(nproc) \
 && make install \
 && ln -sf /usr/local/bin/python3.14 /usr/local/bin/python \
 && ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip \
 && ln -sf /usr/local/bin/idle3.14 /usr/local/bin/idle \
 && ln -sf /usr/local/bin/pydoc3.14 /usr/local/bin/pydoc \
 && ln -sf /usr/local/bin/python3.14-config /usr/local/bin/python-config

export PIP_ROOT_USER_ACTION=ignore

pip install --upgrade pip
pip install uv