#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

service="badminton"
current_image="badminton:current"
previous_image="badminton:previous"
legacy_image="badminton-badminton:latest"

# Preserve the image currently deployed on the first run, then rotate current -> previous.
if docker image inspect "$previous_image" >/dev/null 2>&1; then
  docker image rm "$previous_image" >/dev/null 2>&1 || true
fi

source_image=""
if docker inspect badminton >/dev/null 2>&1; then
  source_image="$(docker inspect --format '{{.Image}}' badminton)"
elif docker image inspect "$current_image" >/dev/null 2>&1; then
  source_image="$current_image"
elif docker image inspect "$legacy_image" >/dev/null 2>&1; then
  source_image="$legacy_image"
fi

if [[ -n "$source_image" ]]; then
  docker image tag "$source_image" "$previous_image"
fi

docker compose build "$service"
docker compose up -d --no-build "$service"

# Remove the old Compose-generated tag; containers refer to image IDs, so the running
# container remains valid. The current and previous tags keep the two rollback versions.
docker image rm "$legacy_image" >/dev/null 2>&1 || true

# Remove every older image for this app that is not the current or rollback version.
# Match the app label or its historical Docker command; unrelated services and base images
# remain untouched. Removal failures are non-fatal after the new container is running.
current_id="$(docker image inspect --format '{{.Id}}' "$current_image")"
previous_id="$(docker image inspect --format '{{.Id}}' "$previous_image" 2>/dev/null || true)"
historical_command='["gunicorn","-w","1","--threads","4","-b","0.0.0.0:8000","badminton:create_app()"]'
while IFS= read -r image_id; do
  [[ -z "$image_id" || "$image_id" == "$current_id" || "$image_id" == "$previous_id" ]] && continue
  app_label="$(docker image inspect --format '{{ index .Config.Labels "com.jmllem.image" }}' "$image_id" 2>/dev/null || true)"
  signature="$(docker image inspect --format '{{.Config.WorkingDir}}|{{json .Config.Cmd}}' "$image_id" 2>/dev/null || true)"
  if [[ "$app_label" == "badminton" || "$signature" == "/app|$historical_command" ]]; then
    if ! docker image rm --force "$image_id" >/dev/null 2>&1; then
      echo "경고: 이전 앱 이미지 $image_id 정리를 완료하지 못했습니다." >&2
    fi
  fi
done < <(docker image ls --quiet --no-trunc | sort -u)

echo "배포 완료: $current_image (이전 버전: $previous_image)"
