# 배포

`main` 반영 후 로컬 저장소에서 앱 파일을 서버로 동기화하고, 서버에서 배포 스크립트를 실행합니다. 운영 설정과 SQLite 데이터는 동기화에서 제외합니다.

```sh
rsync -az --exclude .git --exclude .venv --exclude data --exclude .env --exclude __pycache__ --exclude .omo ./ sigjm@192.168.75.208:badminton/
ssh sigjm@192.168.75.208 'cd badminton && ./scripts/deploy.sh'
```

스크립트는 현재 컨테이너 이미지를 `badminton:previous`로 보존한 뒤 새 이미지를 `badminton:current`로 빌드하고 서비스를 갱신합니다. 첫 실행에서는 실행 중인 기존 컨테이너의 이미지를 직전 버전으로 보존합니다. 배포가 끝나면 현재·직전 버전 태그만 남기고 앱에 해당하는 오래된 이미지와 이전 Compose 태그를 정리합니다. 다른 서비스 이미지와 Docker 빌드 캐시는 유지합니다.

롤백이 필요하면 직전 이미지를 현재 태그에 연결하고 빌드 없이 서비스를 재생성합니다.

```sh
docker image tag badminton:previous badminton:current
docker compose up -d --no-build badminton
```
