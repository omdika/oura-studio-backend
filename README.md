# Oura Studio — FastAPI Backend Service

This is the custom REST API backend service for **Oura Studio**, built with FastAPI and PostgreSQL (Supabase) to handle production scheduling, material nesting optimization, weighted-average HPP calculation, stock ledger auditing, and sales order checkout.

---

## ⚡️ Core Capabilities (Fitur Utama)

1.  **Weighted-Average HPP Engine:**
    *   Saves remaining roll lengths of fabrics in `remaining_length_cm`.
    *   Maintains a weighted-average cost database dynamically computed upon purchase additions.
    *   Locks precise unit COGS upon production batch confirmations.
    *   Implements a three-tier HPP fallback algorithm on checkout if production COGS does not exist yet.
2.  **Cutting Optimizer:**
    *   Computes layout recommendations via a two-phase shelf-packing nesting algorithm (Waste Minimum, Max Qty, Max Profit) respecting fabric width roll constraints.
3.  **Stock Ledger:**
    *   Dual stock ledger keeping audit logs categorized by source (confirmed production batches vs manual adjustments).
4.  **Sales checkout & Reporting:**
    *   Handles multi-item sales checkout.
    *   Generates business metrics for dashboards, sales revenue/profit reports, margin rankings, fabric waste percentages, and low stock alerts.

---

## 🛠 Tech Stack

*   **Framework:** FastAPI (Python 3.10+)
*   **ORM:** SQLAlchemy (with Async/Sync pg-driver support)
*   **Database:** Supabase (PostgreSQL 15+)
*   **Migrations:** Alembic
*   **Authentication:** Google OAuth2 + Custom JWT bearer tokens.

---

## 🚀 Getting Started & Local Setup

### 1. Prerequisites
Ensure you have Python 3.10+ and a virtual environment set up:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create a `.env` file in the root directory based on `.env.example`:
```env
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
SUPABASE_DB_URL="postgresql://postgres:your-db-password@db.your-project.supabase.co:5432/postgres"
JWT_SECRET="your-jwt-secret-key"
GOOGLE_CLIENT_ID="your-google-oauth-client-id"
AUTHORIZED_OWNER_EMAIL="owner@yourdomain.com"
```

### 3. Run Database Migrations
Run Alembic migrations to align database schemas:
```bash
alembic upgrade head
```

### 4. Start the Application
Run the FastAPI development server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
API docs will be available locally at `http://localhost:8000/docs`.

---

## 📁 Project Structure

```text
backend/
├── app/
│   ├── models/         # SQLAlchemy Database models (Product, Material, SalesOrder)
│   ├── routers/        # FastAPI routes (reports.py, sales.py, products.py)
│   ├── schemas/        # Pydantic request/response validation schemas
│   ├── services/       # Business logic (HPP calculations, nesting optimizer)
│   ├── database.py     # SQLAlchemy connection & session local setup
│   └── main.py         # App initialization and middlewares
├── alembic/            # Database migrations histories
└── doc/                # Architectural design and version specification docs
```
