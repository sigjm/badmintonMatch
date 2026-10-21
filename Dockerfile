FROM python:3.12-slim
LABEL com.jmllem.image="badminton"
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 코트 호출 안내 방송 음성 모델 (Supertonic 3, 약 400MB). 실행 중 내려받지 않도록 이미지에 넣는다
RUN python -c "from supertonic import TTS; TTS(auto_download=True)"
COPY badminton /app/badminton
# ponytail: 프로세스 1개 + 스레드 4개. DB 요청은 앱의 잠금으로 한 번에 하나씩, 안내 방송 합성만 따로 돈다.
# 여러 프로세스로 늘리려면 프로세스 사이 잠금(트랜잭션)으로 바꿀 것
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", "badminton:create_app()"]
