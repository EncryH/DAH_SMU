# 베이스 이미지: Python 3.11 (가벼운 버전)
FROM python:3.11-slim

# 컨테이너 안 작업 폴더 설정
WORKDIR /app

# requirements.txt 먼저 복사 + 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# mock_uav.py 복사
COPY uav/mock_uav.py .

# 실행 명령어
ENV PYTHONUNBUFFERED=1
CMD ["python", "mock_uav.py"]