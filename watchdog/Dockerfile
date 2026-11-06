FROM python:3.12-slim

# ssh to ask percula for its containers (the image already has tzdata for CHECK_AT)
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY watchdog.py ./

# The SSH key and known_hosts are mounted read-only on /ssh; /data keeps restart counts
VOLUME /data
ENV PYTHONUNBUFFERED=1

CMD ["python", "watchdog.py"]
