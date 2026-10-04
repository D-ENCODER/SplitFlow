# 🚀 Production Deployment Guide

This guide covers deploying the **Superstore Household Splitter** across various production environments (Docker Compose, Linux VM with Systemd, or Tailscale Private Network).

---

## 🐳 Option 1: Docker Deployment (Recommended)

### Prerequisites
- Docker & Docker Compose installed.

### Quick Start
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/DjangoProject.git
   cd DjangoProject
   ```

2. Configure environment variables in `.env`:
   ```bash
   SECRET_KEY="your-secure-production-key"
   DEBUG=False
   ALLOWED_HOSTS="localhost,127.0.0.1,your-domain.com,your-tailscale-node.ts.net"
   CSRF_TRUSTED_ORIGINS="https://your-domain.com,https://your-tailscale-node.ts.net:8443"
   ```

3. Build and launch containers:
   ```bash
   docker compose up -d --build
   ```

4. Run database migrations & collect static files:
   ```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py collectstatic --noinput
   ```

5. Create an initial Superuser / Admin:
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

---

## 🐧 Option 2: Linux VM (Ubuntu / Debian) with Gunicorn & Systemd

1. **Install System Dependencies**:
   ```bash
   sudo apt update
   sudo apt install -y python3-venv python3-pip git
   ```

2. **Setup Virtual Environment & Install Requirements**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Database & Assets**:
   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

4. **Setup Systemd Service (`/etc/systemd/system/superstore_splitter.service`)**:
   ```ini
   [Unit]
   Description=Superstore Splitter Gunicorn Daemon
   After=network.target

   [Service]
   User=aizen
   Group=www-data
   WorkingDirectory=/home/aizen/PycharmProjects/DjangoProject
   ExecStart=/home/aizen/PycharmProjects/DjangoProject/.venv/bin/gunicorn \
             --workers 3 \
             --bind 127.0.0.1:8000 \
             DjangoProject.wsgi:application
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

5. **Start Service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now superstore_splitter
   ```

---

## 🔒 Option 3: Secure HTTPS via Tailscale

Tailscale allows seamless, encrypted zero-config access for all household members across devices:
1. Install Tailscale:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. Enable Tailscale Serve for automatic HTTPS certificate termination:
   ```bash
   tailscale serve --https=8443 http://127.0.0.1:8000
   ```
3. Share the tailscale link (`https://<node-name>.<tailnet-name>.ts.net:8443`) with household members!
