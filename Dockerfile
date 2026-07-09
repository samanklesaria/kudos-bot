FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates systemd && \
    rm -rf /var/lib/apt/lists/*

# Install pixi
RUN curl -fsSL https://pixi.sh/install.sh | bash
ENV PATH="/root/.pixi/bin:$PATH"

WORKDIR /opt/kudos

# Install dependencies (cached unless pixi.toml/lock changes)
COPY pixi.toml pixi.lock* ./
RUN pixi install

# App code
COPY . .

# Systemd units
RUN cp systemd/*.service systemd/*.timer /etc/systemd/system/ && \
    systemctl enable kudos-dashboard kudos-backfill.timer \
    kudos-weekly-reminder.timer kudos-queue-processor.timer

EXPOSE 7654

CMD ["/sbin/init"]
