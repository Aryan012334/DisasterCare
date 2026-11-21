# DisasterCare

**India-Based Disaster Management and Emergency Response Coordination System**

DisasterCare is a full-stack Django platform for disaster prevention, early warning, incident response, evacuation coordination and relief management across all Indian states and union territories.

---

## Features

### India-Wide Geographic Coverage
- All 28 states and 8 union territories with 144+ districts seeded
- Interactive India map (Leaflet) showing active incidents, risk zones and shelters
- District-level drill-down and geographic filtering

### Disaster Risk Assessment
- 15 pre-seeded disaster risk zones across India (flood, cyclone, earthquake, drought, landslide, heatwave, forest fire, tsunami)
- Risk scoring (0–100), population-at-risk tracking, historical records
- Mitigation task management with progress tracking
- Risk level classification: Low / Moderate / High / Very High

### Early Warning System
- Issue and manage official warnings (NDMA SACHET, IMD, CWC, INCOIS or manual)
- Warning severity levels: Advisory, Watch, Warning, Extreme Alert
- State and district targeting
- In-app notification delivery

### Incident Reporting & Rescue Coordination
- Normal and emergency (fast-track) incident reporting with GPS
- Full workflow: Report → Verify → Dispatch → On Scene → Stabilize → Escalate → Close
- Responder assignment, acceptance and declination
- Duplicate detection, fake-report moderation
- AI-based triage (severity, incident type, risk score)
- Photo and media attachments

### Evacuation & Shelter Management
- 10 pre-seeded emergency shelters across India
- Real-time capacity and occupancy tracking
- Facility flags: food, water, electricity, sanitation, medical, wheelchair access
- Evacuation plan creation, shelter assignment and plan activation

### Relief Resource Management
- Inventory across 5 categories: Food & Water, Medical, Clothing, Rescue Equipment, Communication
- 16 pre-seeded resources with stock levels
- Resource request → approve → dispatch → deliver workflow
- Atomic reservation to prevent double-allocation
- Low-stock alerts

### Role-Based Dashboards
- **National Admin**: India-wide map, incident type breakdown, state-wise counts, warnings, low-stock alerts
- **Responder**: Assignment queue, active commitments, rankings, map
- **Reporter**: My reports, status tracking, notifications

### Additional
- OTP-based password reset via email
- Responder upgrade requests and approval
- Status enquiry workflow with overdue action
- Notification feed with real-time badge updates

---

## Quick Start

### Prerequisites
- Python 3.11+
- pip

### Setup

```bash
git clone https://github.com/Aryan012334/DisasterCare.git
cd DisasterCare

pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in DJANGO_EMAIL_HOST_USER and DJANGO_EMAIL_HOST_PASSWORD

python manage.py migrate
python manage.py seed_india_data
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000 and log in with your superuser account.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Secret key (required in production) | Dev key |
| `DJANGO_DEBUG` | Debug mode | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts | `127.0.0.1,localhost` |
| `DJANGO_EMAIL_HOST_USER` | SMTP email address | — |
| `DJANGO_EMAIL_HOST_PASSWORD` | SMTP password | — |
| `DJANGO_EMAIL_HOST` | SMTP host | `smtp.gmail.com` |
| `DJANGO_EMAIL_PORT` | SMTP port | `587` |

See `.env.example` for the full list.

---

## Seed Data

The `seed_india_data` command loads:
- 36 states and union territories
- 144 districts across major states
- 15 disaster risk zones (flood, cyclone, earthquake, etc.)
- 10 emergency shelters
- 16 relief resources across 5 categories
- 3 demo early warnings

Re-run safely at any time — it uses `update_or_create` and will not duplicate records.

---

## Data Sources

- **Geographic data**: Coordinates from public domain India administrative boundary records
- **Risk zones**: Based on NDMA hazard zonation maps and published vulnerability atlases
- **Early warnings**: Manual import; integration-ready for NDMA SACHET, IMD, CWC and INCOIS APIs
- **Demo data**: Clearly labelled; replace with live data for production

---

## Deployment Notes

For production:
- Set `DJANGO_DEBUG=False` and `DJANGO_SECRET_KEY` to a strong random value
- Set `DJANGO_ALLOWED_HOSTS` to your domain
- Run `python manage.py collectstatic`
- Use gunicorn + nginx or any WSGI host
- Switch `DATABASES` to PostgreSQL for production workloads

---

## Tech Stack

- **Backend**: Django 6.x, SQLite (dev) / PostgreSQL (prod)
- **Frontend**: Bootstrap 5, Chart.js, Leaflet (maps), Font Awesome
- **Maps**: OpenStreetMap tiles via Leaflet
- **Auth**: Email-based login, OTP password reset
