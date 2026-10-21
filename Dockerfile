FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY badminton /app/badminton
# ponytail: 워커 1개라 모든 요청이 순서대로 처리됨 → 경기 끝 동시 클릭 경쟁 없음. 모임이 여러 개로 늘면 트랜잭션 잠금으로 바꿀 것
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "badminton:create_app()"]
