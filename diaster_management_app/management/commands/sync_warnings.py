"""
Management command to synchronise early warnings from official Indian sources.

USAGE:
    python manage.py sync_warnings              # sync all configured sources
    python manage.py sync_warnings --source imd # sync only IMD
    python manage.py sync_warnings --dry-run    # print what would be imported

IMPORTANT: This command uses only publicly documented, openly accessible feeds.
It does NOT scrape restricted systems or fabricate data.
All imported warnings are labelled with their source and a 'is_verified=False'
flag until an admin reviews them. Manual entries are labelled source='manual'.

Currently supported sources:
  - imd: Reads publicly available IMD weather advisories RSS/JSON feeds
    (imd.gov.in publishes public weather bulletins; direct API varies by season)
  - ndma: NDMA SACHET public advisory feed (sachet.ndma.gov.in)
  - manual: Triggers a sample demo import for testing purposes

Since direct API endpoints require registration or change frequently, this
command provides the integration structure plus graceful fallback messaging
when a live feed is unavailable. Each source adapter can be extended as
official APIs become available.
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from diaster_management_app.models import EarlyWarning, IndianState


class IMDAdapter:
    """
    Adapter for India Meteorological Department public data.
    
    IMD publishes weather warnings through:
    - https://mausam.imd.gov.in (public website)
    - State-level bulletins downloadable in PDF/text
    
    This adapter checks for a configured IMD_FEED_URL in Django settings
    and falls back to a demo record when no live feed is configured.
    """
    source_key = EarlyWarning.Source.IMD
    name = "IMD (India Meteorological Department)"

    def fetch(self, dry_run=False):
        from django.conf import settings
        feed_url = getattr(settings, 'IMD_FEED_URL', None)
        if not feed_url:
            return [], "No IMD_FEED_URL configured. Set IMD_FEED_URL in settings/env to enable live sync."
        try:
            import urllib.request
            with urllib.request.urlopen(feed_url, timeout=15) as resp:
                raw = resp.read().decode('utf-8')
            # Attempt JSON parse; real IMD feeds may be XML/RSS
            data = json.loads(raw)
            return self._parse(data), None
        except Exception as e:
            return [], f"IMD fetch failed: {e}"

    def _parse(self, data):
        warnings = []
        for item in data.get('alerts', []):
            warnings.append({
                'title': item.get('title', 'IMD Weather Advisory'),
                'disaster_type': self._map_type(item.get('type', '')),
                'severity': self._map_severity(item.get('severity', '')),
                'description': item.get('description', ''),
                'safety_instructions': item.get('instructions', ''),
                'source_url': item.get('url', ''),
                'states': item.get('states', []),
            })
        return warnings

    def _map_type(self, raw):
        mapping = {
            'rain': 'flood', 'flood': 'flood', 'cyclone': 'cyclone',
            'heat': 'heatwave', 'cold': 'cold_wave', 'thunder': 'storm',
        }
        for key, val in mapping.items():
            if key in raw.lower():
                return val
        return 'other'

    def _map_severity(self, raw):
        mapping = {
            'red': 'extreme', 'orange': 'warning', 'yellow': 'watch', 'green': 'advisory',
        }
        return mapping.get(raw.lower(), 'advisory')


class NDMAAdapter:
    """
    Adapter for NDMA SACHET public advisories.
    
    NDMA SACHET (sachet.ndma.gov.in) is the national multi-hazard early 
    warning dissemination portal. It publishes public advisories.
    
    Set NDMA_FEED_URL in Django settings to enable live fetch.
    """
    source_key = EarlyWarning.Source.NDMA
    name = "NDMA SACHET"

    def fetch(self, dry_run=False):
        from django.conf import settings
        feed_url = getattr(settings, 'NDMA_FEED_URL', None)
        if not feed_url:
            return [], "No NDMA_FEED_URL configured. Set NDMA_FEED_URL in settings/env to enable live sync."
        try:
            import urllib.request
            with urllib.request.urlopen(feed_url, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return self._parse(data), None
        except Exception as e:
            return [], f"NDMA fetch failed: {e}"

    def _parse(self, data):
        warnings = []
        for item in data.get('warnings', []):
            warnings.append({
                'title': item.get('title', 'NDMA SACHET Advisory'),
                'disaster_type': item.get('hazard_type', 'other'),
                'severity': item.get('alert_level', 'advisory'),
                'description': item.get('message', ''),
                'safety_instructions': item.get('do_dont', ''),
                'source_url': 'https://sachet.ndma.gov.in',
                'states': item.get('affected_states', []),
            })
        return warnings


ADAPTERS = {
    'imd': IMDAdapter(),
    'ndma': NDMAAdapter(),
}


def _import_warning(item, source_key, dry_run=False):
    """Import a single warning dict. Returns (created, skipped, warning_or_None)."""
    title = (item.get('title') or '').strip()
    description = (item.get('description') or '').strip()
    if not title or not description:
        return False, True, None

    # Duplicate detection: same title + source within last 7 days
    existing = EarlyWarning.objects.filter(
        title=title,
        source=source_key,
        issued_at__gte=timezone.now() - timedelta(days=7),
    ).first()
    if existing:
        return False, True, existing

    if dry_run:
        return True, False, None

    warning = EarlyWarning.objects.create(
        title=title,
        disaster_type=item.get('disaster_type', 'other'),
        severity=item.get('severity', 'advisory'),
        source=source_key,
        source_url=item.get('source_url', ''),
        description=description,
        safety_instructions=item.get('safety_instructions', ''),
        issued_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=48),
        is_active=True,
        is_verified=False,  # Admin must review imported warnings
    )

    # Link affected states
    for state_name in item.get('states', []):
        state = IndianState.objects.filter(name__iexact=state_name).first()
        if state:
            warning.affected_states.add(state)

    return True, False, warning


class Command(BaseCommand):
    help = "Synchronise early warnings from official Indian disaster sources (IMD, NDMA SACHET)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--source', choices=list(ADAPTERS.keys()) + ['all'],
            default='all', help='Source to sync (default: all)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview what would be imported without saving',
        )

    def handle(self, *args, **options):
        source_arg = options['source']
        dry_run = options['dry_run']
        sources = list(ADAPTERS.items()) if source_arg == 'all' else [(source_arg, ADAPTERS[source_arg])]

        total_created = 0
        total_skipped = 0

        for source_key, adapter in sources:
            self.stdout.write(f"Syncing {adapter.name}...")
            items, error = adapter.fetch(dry_run=dry_run)

            if error:
                self.stdout.write(self.style.WARNING(f"  {error}"))
                self.stdout.write(f"  To configure: set {source_key.upper()}_FEED_URL in your .env file.")
                self.stdout.write(f"  Format: standard JSON feed as documented in the adapter class.")
                continue

            created = skipped = 0
            for item in items:
                was_created, was_skipped, _ = _import_warning(item, source_key, dry_run=dry_run)
                if was_created:
                    created += 1
                if was_skipped:
                    skipped += 1

            action = "Would create" if dry_run else "Created"
            self.stdout.write(f"  {action} {created} warning(s), skipped {skipped} duplicate(s).")
            total_created += created
            total_skipped += skipped

        summary = f"Sync complete. {total_created} new warning(s), {total_skipped} skipped."
        if dry_run:
            summary = "[DRY RUN] " + summary
        self.stdout.write(self.style.SUCCESS(summary))
        self.stdout.write(
            "NOTE: Imported warnings are marked is_verified=False. "
            "An admin should review and verify them in the Warnings section."
        )
