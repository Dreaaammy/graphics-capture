#!/usr/bin/env bash
set -euo pipefail

glxinfo -B > artifacts/opengl-driver.txt
vulkaninfo --summary > artifacts/vulkan-driver.txt 2>&1 || true
/usr/games/supertuxkart --help > artifacts/game-options.txt 2>&1
/usr/bin/python3 -u scripts/remote_capture.py --driver "${1:-gl}" 2>&1 | tee artifacts/capture-log.txt
