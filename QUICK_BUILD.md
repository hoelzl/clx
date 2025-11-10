# Quick Build Reference

## The Problem You Encountered

When building from a service directory:
```bash
cd services/drawio-converter
docker build -t clx-drawio-converter .
```

This fails because Docker cannot access files outside the build context (like `../../clx-common`).

## The Solution

**Always build from the project root** using one of these methods:

### Method 1: Use the Build Script (Easiest)

```bash
# From project root
./build-services.sh drawio-converter
```

### Method 2: Direct Docker Build

```bash
# From project root
export DOCKER_BUILDKIT=1

docker build \
  -f services/drawio-converter/Dockerfile \
  -t clx-drawio-converter \
  --build-arg SERVICE_PATH=services/drawio-converter \
  --build-arg COMMON_PATH=. \
  .
```

Notice the final `.` - this sets the build context to the current directory (project root).

## Build All Services

```bash
./build-services.sh
```

## Why This Works

- The `.` at the end sets the **build context** to the project root
- This gives Docker access to both `services/` and `clx-common/`
- The `-f` flag specifies which Dockerfile to use
- The build args tell the Dockerfile where to find files relative to the root

See `BUILD.md` for complete documentation including cache management.
