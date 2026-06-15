#!/usr/bin/env bash
# Build the DocSensei image with a build-time version stamp.
#
# NOTE: VERSION + CHANGELOG are bumped MANUALLY in app/version.py before
# running this. This script only stamps BUILD_SHA + BUILD_DATE into the image.
set -euo pipefail

SHA=$(date +%s | sha1sum | cut -c1-7)
DATE=$(date +%Y-%m-%d)
echo "Building DocSensei  sha=$SHA  date=$DATE"
docker compose build --build-arg BUILD_SHA="$SHA" --build-arg BUILD_DATE="$DATE" app
echo "Now: docker compose up -d --force-recreate app"
