<div align="center">

# 🛒 SplitFlow :: Superstore Household Splitter

**The intelligent, multi-tenant household expense sharing platform & automated Real Canadian Superstore receipt parser.**

[![Django](https://img.shields.io/badge/Django-6.1-0C4B33?style=for-the-badge&logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com/)
[![PWA Ready](https://img.shields.io/badge/PWA-Ready-orange?style=for-the-badge&logo=pwa&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Build Status](https://img.shields.io/badge/Tests-Passing%20(11%2F11)-brightgreen?style=for-the-badge)](https://github.com/)

<p align="center">
  <img src="docs/images/dashboard_preview.jpg" alt="SplitFlow Dashboard Mockup" width="850" style="border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,0.12);" />
</p>

</div>

---

## 🌟 Key Highlights

SplitFlow was built from the ground up to eliminate the friction of shared apartment living, group trips, and grocery bill splitting:

- **⚡ Zero-Data Onboarding**: A brand new user with no prior data can register in seconds. The platform automatically creates their primary household, configures permissions, and brings them to a pristine, crash-free `$0.00` dashboard.
- **👑 Group Admin Governance**: The creator of a household or group is automatically crowned **Group Admin (👑)**. Group Admins have exclusive authority to rename households, add/remove members, and manage group settings.
- **🔒 Server-Enforced Date Integrity**: Expenses strictly record **Today's Date** enforced server-side. Users cannot retrospectively backdate transactions (past dates can be referenced in the notes section).
- **🧮 Debt Simplification Engine**: Implements a graph-theoretic greedy **Min-Cash-Flow algorithm** that simplifies complex multi-party debt cycles into the fewest possible transactions.
- **🧾 Superstore Receipt Ingestion**: Directly parses digital Real Canadian Superstore (PC Express) orders with CDN thumbnails, item quantities, sales taxes, and automated bottle deposit reconciliation down to the penny.
- **📥 Splitwise Migration**: 1-click import from Splitwise CSV exports, instantly recreating historical ledgers, transactions, and roommate balances.
- **📊 Real-time Visual Analytics**: Clean spending breakdowns, category metrics, and roommate debt distribution graphs.
- **📱 Progressive Web App (PWA)**: Installable on iOS, Android, and Desktop with full-screen standalone mode and offline shell caching.

---

## 🖼️ Automated Superstore Receipt Breakdown

<div align="center">
  <img src="docs/images/receipt_parser_preview.jpg" alt="Superstore Receipt Parser" width="850" style="border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,0.12);" />
</div>

SplitFlow's itemizer handles complex grocery bills:
- Extracts actual product titles, package weights, and quantities.
- Matches and links high-resolution product images directly from Superstore CDNs.
- Reconciles bottle deposits, environmental handling fees, and GST/PST so the split matches the credit card charge.
- Allows 1-click item-by-item assignment or shared splits across roommates.

---

## 🚀 Quick Start

### Option A: Local Development

```bash
# 1. Clone the repository
git clone https://github.com/your-username/DjangoProject.git
cd DjangoProject

# 2. Check out the development branch
git checkout dev

# 3. Create virtual environment & install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Run migrations & collect static files
python manage.py migrate
python manage.py collectstatic --noinput

# 5. Run automated test suite
python manage.py test rcsscrapper

# 6. Launch development server
python manage.py runserver 0.0.0.0:8000
```

### Option B: Docker Compose

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
```

Open [http://localhost:8000](http://localhost:8000) in your browser!

---

## 📚 Documentation & Wiki

Comprehensive technical manuals and user guides are available in the [`docs/`](docs/) directory:

| Document | Description |
| :--- | :--- |
| [**📖 User Guide**](docs/USER_GUIDE.md) | Complete walkthrough of accounts, group administration, adding expenses, settling up, and PWA setup. |
| [**🏗️ Architecture Spec**](docs/ARCHITECTURE.md) | Deep dive into data models, Min-Cash-Flow algorithm, scoping isolation, and security guarantees. |
| [**🚀 Deployment Guide**](docs/DEPLOYMENT.md) | Production setup with Docker, Linux Systemd, Gunicorn, Nginx, and Tailscale HTTPS. |

---

## 🌿 Git Branching Strategy

This project follows professional Git branching practices:

- **`main`**: Production-ready, rock-solid code deployed in live environments.
- **`dev`**: Active development, feature iterations, and integration testing.

```
       (Feature / Test)
         /---------\
dev ----*-----------*-----> (Next Features)
                     \
main -----------------*---> (Production Releases)
```

---

## 🧪 Testing & Verification

Run the comprehensive unit, permission, and stress test suite:

```bash
python manage.py test rcsscrapper
```

### Test Coverage Highlights:
- **`OpenSourceNewUserZeroDataTestCase`**: Registration, automated household group creation, clean zero-data state, server-side date enforcement.
- **`GroupAdminPermissionsTestCase`**: Group Admin permissions, member add/remove, intrusion rejection, owner self-removal protection.
- **`SplitwiseAlgorithmAndStressTestCase`**: Multi-party cyclical debt simplification, matrix conservation, and high-volume 12-member/100-split stress performance.
- **`SuperstoreParserAndReconciliationTestCase`**: HTML itemization, bottle deposit reconciliation ($83.20 order verification), and 404-free graceful deletion.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.
