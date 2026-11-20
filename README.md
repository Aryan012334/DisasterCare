# CRDR (Django Disaster Response Platform)

CRDR is a Django-based incident response system with role-based workflows for:
- Reporter users (report incidents)
- Responder users (accept tasks, update field status, submit final review)
- Admin users (verify, dispatch, moderate, and close incidents)

## Features
- Authentication with role-aware registration and approval flow
- Incident reporting (normal and emergency)
- Responder assignment and live status workflow
- Escalation flow with nearby responder notifications
- Final review and admin closure workflow
- Fake incident review/moderation flow
- Password reset with OTP

## Project Structure
- `manage.py` - Django entry point
- `rdms/` - project settings and URLs
- `diaster_management_app/` - main app (models, views, forms, tests)
- `templates/` - HTML templates
- `static/` - static assets
- `media/` - uploaded files (ignored in git)

## Local Setup

### 1. Create and activate virtual environment
Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
```

For local development without SMTP credentials, set:
- `DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`

This avoids startup errors when `DJANGO_DEBUG=True`.

### 4. Apply migrations
```bash
python manage.py migrate
```

### 5. (Optional) Create admin user
```bash
python manage.py createsuperuser
```

### 6. Run server
```bash
python manage.py runserver
```

## Run Tests
```bash
python manage.py test
```

## Git Upload Checklist
- Keep secrets out of git (`.env` is ignored).
- Keep runtime/local files out of git (`db.sqlite3`, `media/`, caches are ignored).
- Review `.gitignore` before first commit.

Optional cleanup before first push:

Windows (PowerShell):
```powershell
Get-ChildItem -Recurse -Directory -Filter __pycache__
Get-ChildItem -Recurse -File -Include *.pyc
```

Linux/macOS:
```bash
find . -type d -name "__pycache__"
find . -type f -name "*.pyc"
```

## Notes
- Main app folder is named `diaster_management_app` (spelling kept as-is in current codebase).
- Default DB is SQLite (`db.sqlite3`).
