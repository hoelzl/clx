# Docker Build Guide

## Overview

The Docker containers for CLX services use **BuildKit cache mounts** to optimize build times and minimize package downloads. This approach caches downloaded packages (pip, conda, apt) on your build machine, so they only need to be downloaded once.

## Key Features

- **No separate base images needed**: All dependencies are built directly into service images
- **Persistent caching**: Packages are cached locally and reused across builds
- **Layer optimization**: Requirements are installed before service code, so code changes don't trigger dependency re-installs
- **Multi-layer caching**: Separate caches for pip, conda/mamba, and apt packages

## Prerequisites

You must use **Docker BuildKit** to build these containers. BuildKit is the default in Docker 20.10+, but you can ensure it's enabled:

```bash
# Set environment variable for single build
export DOCKER_BUILDKIT=1
docker build ...

# Or enable it permanently in Docker daemon config
# Add to /etc/docker/daemon.json:
{
  "features": {
    "buildkit": true
  }
}
```

## Building Services

**IMPORTANT**: All builds must be run from the **root directory** of the project, as the Dockerfiles need access to both the service code and the shared `clx-common` directory.

### Build from Root Directory

Build each service from the project root:

```bash
# From the root of the clx project
export DOCKER_BUILDKIT=1

# Build drawio-converter
docker build \
  -f services/drawio-converter/Dockerfile \
  -t clx-drawio-converter \
  --build-arg SERVICE_PATH=services/drawio-converter \
  --build-arg COMMON_PATH=. \
  .

# Build notebook-processor
docker build \
  -f services/notebook-processor/Dockerfile \
  -t clx-notebook-processor \
  --build-arg SERVICE_PATH=services/notebook-processor \
  --build-arg COMMON_PATH=. \
  .

# Build plantuml-converter
docker build \
  -f services/plantuml-converter/Dockerfile \
  -t clx-plantuml-converter \
  --build-arg SERVICE_PATH=services/plantuml-converter \
  --build-arg COMMON_PATH=. \
  .
```

### Using the Build Script

For convenience, use the provided build script:

```bash
# Build all services
./build-services.sh

# Build specific service
./build-services.sh drawio-converter
./build-services.sh notebook-processor
./build-services.sh plantuml-converter
```

## How Caching Works

### Pip Cache

Python packages are cached in `/root/.cache/pip`:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

When you rebuild, pip will:
1. Check the cache for already-downloaded packages
2. Only download new or updated packages
3. Install from the local cache when possible

### Conda/Mamba Cache

For the notebook-processor, conda packages are cached in `/opt/conda/pkgs`:

```dockerfile
RUN --mount=type=cache,target=/opt/conda/pkgs \
    micromamba install -y -n base -f packages.yaml
```

### Apt Cache

System packages are cached to avoid re-downloading on rebuilds:

```dockerfile
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y <packages>
```

## Cache Location

BuildKit stores caches in Docker's build cache storage. You can:

```bash
# View cache usage
docker buildx du

# Clear all build cache (including our package caches)
docker buildx prune

# Clear only old/unused cache
docker buildx prune --keep-storage 20GB
```

## Rebuild Scenarios

### Service Code Changes Only
- **Fast**: Only the final layers rebuild
- **No downloads**: All dependencies are cached

### Requirements File Changes
- **Medium**: Dependencies layer rebuilds
- **Partial downloads**: Only changed packages download, others use cache

### Base Image or System Dependencies Change
- **Slow**: Most layers rebuild
- **Partial downloads**: Package caches still help

## Migration from Base Images

The previous architecture used separate base images (`docker-base-images/*`). These are **no longer needed** for building services, but are kept for reference. The new Dockerfiles copy necessary files directly from these directories.

If you want to remove the base images:

```bash
# Remove old base images
docker rmi mhoelzl/clx-drawio-converter-base:0.2.0
docker rmi mhoelzl/clx-notebook-processor-base:0.2.0
docker rmi mhoelzl/clx-plantuml-converter-base:0.2.0
```

## Troubleshooting

### Cache Not Working

If packages are re-downloading on every build:

1. **Check BuildKit is enabled**: `docker buildx version`
2. **Verify syntax directive**: Ensure Dockerfiles start with `# syntax=docker/dockerfile:1`
3. **Check cache mounts**: Look for `--mount=type=cache` in RUN commands

### Cache Taking Too Much Space

```bash
# Check cache size
docker system df -v

# Prune build cache older than 7 days
docker buildx prune --filter until=168h

# Set a size limit (keeps most recent)
docker buildx prune --keep-storage 10GB
```

### Permission Issues

If you see permission errors with cache mounts, ensure you're building as root in the Dockerfile (default for most base images).

## Performance Tips

1. **Keep requirements stable**: Changes to requirements.txt trigger re-installs
2. **Order matters**: Copy requirements before service code
3. **Combine RUN commands carefully**: Each RUN creates a layer, but too many cache mounts in one RUN can be slower
4. **Use .dockerignore**: Prevent unnecessary files from invalidating cache

## Additional Notes

- The `--no-cache-dir` flag has been **removed** from pip commands to enable caching
- Apt cache uses `sharing=locked` to allow parallel builds
- Large downloads (PyTorch, CUDA, etc.) benefit most from caching
- Cache is per-build-machine, not included in the image
