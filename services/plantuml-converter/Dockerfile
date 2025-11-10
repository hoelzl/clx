# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

ARG SERVICE_PATH=.
ARG COMMON_PATH=../..

# Copy requirements and PlantUML jar first (for better layer caching)
COPY ${COMMON_PATH}/docker-base-images/plantuml-converter-base/plantuml-1.2024.6.jar ./plantuml.jar
COPY ${COMMON_PATH}/docker-base-images/plantuml-converter-base/requirements.txt ./base-requirements.txt

# Install system dependencies (Java for PlantUML)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y \
        default-jre \
        dbus \
        graphviz

# Install Python dependencies with cache mount
# This caches pip downloads on the build machine, avoiding re-downloads
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r base-requirements.txt

# Copy service code and install
COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./plantuml-converter

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx-common && \
    pip install ./plantuml-converter

ENTRYPOINT ["python", "-m", "plantuml_converter.plantuml_converter"]
