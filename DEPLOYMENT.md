# SplitFlow Production Deployment Guide

This guide covers deployment procedures for **SplitFlow** across standard Linux hosting architectures, including containerized Docker Compose setups, standalone Linux VMs with Systemd and Gunicorn, and private mesh networking via Tailscale HTTPS.

---

## 1. Prerequisites and Environment Configuration

### Required Software
- Python 3.12 or newer
- Docker and Docker Compose (if deploying containerized)
- Git

### Environment Variables
Configure the following variables in a `.env` file located at the repository root:

```ini
# Production Secret Key (generate a 50+ character cryptographic random string)
SECRET_KEY="generate-a-strong-random-production-secret-key"

# Security Settings
DEBUG=False
ALLOWED_HOSTS="127.0.0.1,localhost,your-domain.com,your-tailscale-node.ts.net"
CSRF_TRUSTED_ORIGINS="https://your-domain.com,https://your-tailscale-node.ts.net:8443"

# Database Configuration (Defaults to SQLite; configure PostgreSQL via DATABASE_URL if preferred)
# DATABASE_URL="postgres://splitflow_user:password@localhost:5432/splitflow_db"
```

---

## 2. Option A: Containerized Deployment via Docker Compose (Recommended)

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/D-ENCODER/SplitFlow.git
   cd SplitFlow
   git checkout main
   ```

2. **Launch Application Containers**:
   ```bash
   docker compose up -d --build
   ```

3. **Execute Database Migrations and Static Collection**:
   ```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py collectstatic --noinput
   ```

4. **Initialize Superuser Account**:
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

---

## 3. Option B: Standalone Linux VM (Ubuntu / Debian) with Gunicorn and Systemd

1. **Install System Dependencies**:
   ```bash
   sudo apt update
   sudo apt install -y python3-venv python3-pip git nginx
   ```

2. **Clone and Configure Application Directory**:
   ```bash
   git clone https://github.com/D-ENCODER/SplitFlow.git /var/www/splitflow
   cd /var/www/splitflow
   git checkout main

   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Initialize Database and Static Storage**:
   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

4. **Configure Systemd Service Unit (`/etc/systemd/system/splitflow.service`)**:
   ```ini
   [Unit]
   Description=SplitFlow Gunicorn WSGI Daemon
   After=network.target

   [Service]
   User=www-data
   Group=www-data
   WorkingDirectory=/var/www/splitflow
   ExecStart=/var/www/splitflow/.venv/bin/gunicorn \
             --workers 3 \
             --bind 127.0.0.1:8000 \
             --access-logfile - \
             --error-logfile - \
             rcsscrapper.wsgi:application
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

5. **Start and Enable Service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now splitflow
   ```

---

## 4. Option C: Encrypted Private Mesh Hosting via Tailscale

Tailscale allows deploying SplitFlow securely within a private zero-trust network without exposing ports publicly to the internet.

1. **Install and Authenticate Tailscale**:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

2. **Configure Tailscale Serve for Automatic HTTPS**:
   ```bash
   tailscale serve --https=8443 http://127.0.0.1:8000
   ```

3. **Access Endpoint**:
   Share the generated TLS endpoint with authenticated household members:
   `https://<node-name>.<tailnet-name>.ts.net:8443`

---

## 5. Maintenance and Backup Procedures

### Database Backups
For SQLite deployments, execute atomic backups using the SQLite online backup utility:
```bash
sqlite3 /var/www/splitflow/db.sqlite3 ".backup '/var/backups/splitflow_$(date +%Y%m%d).sqlite3'"
```

### Upgrading to New Releases
```bash
cd /var/www/splitflow
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart splitflow
```
