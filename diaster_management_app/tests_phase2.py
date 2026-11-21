"""
Phase 2 tests for DisasterCare – security, permissions, workflows, and new features.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    DisasterRiskZone, EarlyWarning, EvacuationPlan, Incident,
    IndianDistrict, IndianState, MitigationTask, ReliefResource,
    ResourceCategory, ResourceRequest, Shelter, UserProfile,
    LocationSubscription,
)


User = get_user_model()


def make_state(name='Test State', code='TS1'):
    return IndianState.objects.get_or_create(
        code=code,
        defaults={'name': name, 'latitude': 20.0, 'longitude': 78.0}
    )[0]


def make_district(state, name='Test District'):
    return IndianDistrict.objects.get_or_create(
        state=state, name=name
    )[0]


def make_reporter(username='reporter1'):
    u = User.objects.create_user(username=username, email=f'{username}@test.com', password='pass12345', is_active=True)
    u.profile.role = UserProfile.Role.REPORTER
    u.profile.verification_status = UserProfile.VerificationStatus.APPROVED
    u.profile.save(update_fields=['role', 'verification_status', 'updated_at'])
    return u


def make_admin(username='admin1'):
    return User.objects.create_superuser(username=username, email=f'{username}@test.com', password='pass12345')


def make_incident(reporter, **kwargs):
    defaults = dict(
        title='Test Incident',
        incident_type=Incident.IncidentType.FLOOD,
        severity=Incident.Severity.MEDIUM,
        description='Test',
        address='Test Location',
        people_affected=5,
        affected_radius_km=2.0,
        latitude='20.000000',
        longitude='78.000000',
        status=Incident.Status.PENDING,
        report_mode=Incident.ReportMode.NORMAL,
    )
    defaults.update(kwargs)
    return Incident.objects.create(reporter=reporter, **defaults)


class SecurityTests(TestCase):
    """Test that sensitive endpoints require authentication."""

    def setUp(self):
        self.admin = make_admin('sec_admin')
        self.reporter = make_reporter('sec_reporter')

    def test_india_map_data_requires_login(self):
        resp = self.client.get(reverse('disaster_management:india_map_data'))
        self.assertIn(resp.status_code, [302, 403])
        if resp.status_code == 302:
            self.assertIn('/login/', resp.url)

    def test_india_map_data_reporter_does_not_get_incident_coordinates(self):
        inc = make_incident(self.reporter, latitude='22.123456', longitude='88.654321')
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:india_map_data'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for item in data.get('incidents', []):
            self.assertNotIn('latitude', item, "Reporter should not receive incident coordinates")
            self.assertNotIn('longitude', item, "Reporter should not receive incident coordinates")

    def test_india_map_data_admin_gets_full_incident_data(self):
        inc = make_incident(self.reporter, latitude='22.123456', longitude='88.654321')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('disaster_management:india_map_data'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        incidents = data.get('incidents', [])
        # Admin should get coordinates
        if incidents:
            self.assertIn('latitude', incidents[0])

    def test_national_dashboard_requires_admin(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:national_dashboard'))
        self.assertEqual(resp.status_code, 302)

    def test_national_dashboard_accessible_to_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('disaster_management:national_dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_risk_zone_create_requires_admin(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:risk_zone_create'), data={
            'name': 'Hacked Zone', 'hazard_type': 'flood', 'risk_level': 'high',
            'latitude': '20.0', 'longitude': '78.0', 'radius_km': '10',
        })
        self.assertEqual(DisasterRiskZone.objects.filter(name='Hacked Zone').count(), 0)

    def test_warning_create_requires_admin(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:warning_create'), data={
            'title': 'Hacked Warning', 'disaster_type': 'flood',
            'severity': 'extreme', 'description': 'Fake',
        })
        self.assertEqual(EarlyWarning.objects.filter(title='Hacked Warning').count(), 0)

    def test_shelter_create_requires_admin(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:shelter_create'), data={
            'name': 'Hacked Shelter', 'latitude': '20.0', 'longitude': '78.0', 'capacity': '100',
        })
        self.assertEqual(Shelter.objects.filter(name='Hacked Shelter').count(), 0)

    def test_resource_request_action_requires_admin(self):
        cat = ResourceCategory.objects.create(name='TestCat2')
        res = ReliefResource.objects.create(name='TestResource2', unit='units',
            quantity_available=100, category=cat)
        rr = ResourceRequest.objects.create(resource=res, requested_by=self.reporter,
            quantity_requested=10, status=ResourceRequest.Status.PENDING)
        self.client.force_login(self.reporter)
        resp = self.client.post(
            reverse('disaster_management:resource_request_action', args=[rr.id]),
            data={'action': 'approve', 'quantity_approved': '10'}
        )
        rr.refresh_from_db()
        self.assertNotEqual(rr.status, ResourceRequest.Status.APPROVED)

    def test_anonymous_cannot_access_dashboard(self):
        resp = self.client.get(reverse('disaster_management:dashboard_home'))
        self.assertIn(resp.status_code, [302, 403])


class RiskZoneTests(TestCase):
    def setUp(self):
        self.admin = make_admin('rz_admin')
        self.reporter = make_reporter('rz_reporter')
        self.state = make_state('RZ State', 'RZ1')

    def test_risk_zone_list_loads(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:risk_zone_list'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_create_risk_zone(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:risk_zone_create'), data={
            'name': 'Admin Test Zone',
            'hazard_type': 'flood',
            'risk_level': 'high',
            'latitude': '20.5',
            'longitude': '78.5',
            'radius_km': '25',
            'population_at_risk': '50000',
            'description': 'Test zone for unit test',
            'preventive_measures': 'Build embankments',
        })
        self.assertEqual(DisasterRiskZone.objects.filter(name='Admin Test Zone').count(), 1)
        zone = DisasterRiskZone.objects.get(name='Admin Test Zone')
        self.assertEqual(zone.hazard_type, 'flood')
        self.assertEqual(zone.risk_level, 'high')

    def test_risk_zone_detail_loads(self):
        zone = DisasterRiskZone.objects.create(
            name='Detail Zone', hazard_type='earthquake', risk_level='very_high',
            risk_score=85.0, latitude='28.0', longitude='77.0', radius_km=50,
        )
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:risk_zone_detail', args=[zone.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Detail Zone')

    def test_admin_can_add_mitigation_task(self):
        zone = DisasterRiskZone.objects.create(
            name='Task Zone', hazard_type='flood', risk_level='high',
            risk_score=70.0, latitude='25.0', longitude='80.0', radius_km=30,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('disaster_management:mitigation_task_create', args=[zone.id]),
            data={'title': 'Build flood walls', 'priority': 'high', 'assigned_to': 'PWD Dept', 'description': 'Construct protective walls'}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MitigationTask.objects.filter(risk_zone=zone, title='Build flood walls').count(), 1)


class WarningTests(TestCase):
    def setUp(self):
        self.admin = make_admin('warn_admin')
        self.reporter = make_reporter('warn_reporter')
        self.state = make_state('Warn State', 'WS1')

    def test_warning_list_accessible_to_all_logged_in(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:warning_list'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_create_warning(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:warning_create'), data={
            'title': 'Test Flood Warning',
            'disaster_type': 'flood',
            'severity': 'warning',
            'source': 'manual',
            'description': 'Heavy rainfall expected in next 48 hours.',
            'safety_instructions': 'Avoid low-lying areas.',
            'expires_in_hours': '48',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EarlyWarning.objects.filter(title='Test Flood Warning').count(), 1)
        w = EarlyWarning.objects.get(title='Test Flood Warning')
        self.assertTrue(w.is_active)
        self.assertTrue(w.is_verified)
        self.assertIsNotNone(w.expires_at)

    def test_warning_detail_loads(self):
        w = EarlyWarning.objects.create(
            title='Detail Warning', disaster_type='cyclone', severity='extreme',
            source='imd', description='Severe cyclone warning.', is_active=True,
        )
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:warning_detail', args=[w.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Detail Warning')

    def test_admin_can_deactivate_warning(self):
        w = EarlyWarning.objects.create(
            title='Active Warning', disaster_type='flood', severity='advisory',
            source='manual', description='Test.', is_active=True,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:warning_deactivate', args=[w.id]))
        self.assertEqual(resp.status_code, 302)
        w.refresh_from_db()
        self.assertFalse(w.is_active)

    def test_reporter_cannot_deactivate_warning(self):
        w = EarlyWarning.objects.create(
            title='Protected Warning', disaster_type='flood', severity='advisory',
            source='manual', description='Test.', is_active=True,
        )
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:warning_deactivate', args=[w.id]))
        w.refresh_from_db()
        self.assertTrue(w.is_active)


class ShelterTests(TestCase):
    def setUp(self):
        self.admin = make_admin('sh_admin')
        self.reporter = make_reporter('sh_reporter')
        self.state = make_state('Shelter State', 'SS1')

    def test_shelter_list_accessible(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:shelter_list'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_create_shelter(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:shelter_create'), data={
            'name': 'Test Shelter Alpha',
            'address': '123 Relief Road',
            'latitude': '20.5',
            'longitude': '78.5',
            'capacity': '500',
            'managed_by': 'Red Cross',
            'has_food': 'on',
            'has_water': 'on',
            'is_accessible': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Shelter.objects.filter(name='Test Shelter Alpha').count(), 1)
        s = Shelter.objects.get(name='Test Shelter Alpha')
        self.assertEqual(s.capacity, 500)
        self.assertTrue(s.has_food)
        self.assertTrue(s.has_water)

    def test_shelter_detail_loads(self):
        s = Shelter.objects.create(
            name='Detail Shelter', address='Test', latitude='20.0', longitude='78.0',
            capacity=200, status=Shelter.Status.ACTIVE,
        )
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:shelter_detail', args=[s.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Detail Shelter')

    def test_admin_can_update_occupancy(self):
        s = Shelter.objects.create(
            name='Occupancy Shelter', address='Test', latitude='20.0', longitude='78.0',
            capacity=300, current_occupancy=0, status=Shelter.Status.ACTIVE,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('disaster_management:shelter_update_occupancy', args=[s.id]),
            data={'current_occupancy': '150', 'status': 'active'}
        )
        self.assertEqual(resp.status_code, 302)
        s.refresh_from_db()
        self.assertEqual(s.current_occupancy, 150)

    def test_occupancy_cannot_exceed_capacity(self):
        s = Shelter.objects.create(
            name='Cap Shelter', address='Test', latitude='20.0', longitude='78.0',
            capacity=100, current_occupancy=0, status=Shelter.Status.ACTIVE,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('disaster_management:shelter_update_occupancy', args=[s.id]),
            data={'current_occupancy': '999', 'status': 'active'}
        )
        s.refresh_from_db()
        self.assertLessEqual(s.current_occupancy, 100)


class ResourceTests(TestCase):
    def setUp(self):
        self.admin = make_admin('res_admin')
        self.reporter = make_reporter('res_reporter')
        self.cat = ResourceCategory.objects.create(name='TestCatRes')
        self.resource = ReliefResource.objects.create(
            name='Test Food Packets', unit='packets',
            quantity_available=1000, quantity_reserved=0,
            minimum_stock=50, category=self.cat,
        )

    def test_resource_list_loads(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:resource_list'))
        self.assertEqual(resp.status_code, 200)

    def test_reporter_can_submit_resource_request(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:resource_request_create'), data={
            'resource_id': self.resource.id,
            'quantity_requested': '50',
            'purpose': 'Flood relief camp needs food packets',
        })
        self.assertEqual(resp.status_code, 302)
        rr = ResourceRequest.objects.filter(resource=self.resource, requested_by=self.reporter).first()
        self.assertIsNotNone(rr)
        self.assertEqual(rr.quantity_requested, 50)
        self.assertEqual(rr.status, ResourceRequest.Status.PENDING)
        # Reserved quantity should increase
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.quantity_reserved, 50)

    def test_cannot_request_more_than_available(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(reverse('disaster_management:resource_request_create'), data={
            'resource_id': self.resource.id,
            'quantity_requested': '9999',
            'purpose': 'Should fail',
        })
        self.assertEqual(ResourceRequest.objects.filter(resource=self.resource).count(), 0)

    def test_admin_approve_dispatch_deliver_lifecycle(self):
        rr = ResourceRequest.objects.create(
            resource=self.resource, requested_by=self.reporter,
            quantity_requested=100, status=ResourceRequest.Status.PENDING,
        )
        self.resource.quantity_reserved = 100
        self.resource.save()
        initial_available = self.resource.quantity_available
        self.client.force_login(self.admin)

        # Approve
        self.client.post(
            reverse('disaster_management:resource_request_action', args=[rr.id]),
            data={'action': 'approve', 'quantity_approved': '100'}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, ResourceRequest.Status.APPROVED)

        # Dispatch
        self.client.post(
            reverse('disaster_management:resource_request_action', args=[rr.id]),
            data={'action': 'dispatch'}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, ResourceRequest.Status.DISPATCHED)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.quantity_available, initial_available - 100)

        # Deliver
        self.client.post(
            reverse('disaster_management:resource_request_action', args=[rr.id]),
            data={'action': 'deliver'}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, ResourceRequest.Status.DELIVERED)

    def test_reject_releases_reservation(self):
        rr = ResourceRequest.objects.create(
            resource=self.resource, requested_by=self.reporter,
            quantity_requested=200, status=ResourceRequest.Status.PENDING,
        )
        self.resource.quantity_reserved = 200
        self.resource.save()
        self.client.force_login(self.admin)
        self.client.post(
            reverse('disaster_management:resource_request_action', args=[rr.id]),
            data={'action': 'reject', 'admin_note': 'Not justified'}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, ResourceRequest.Status.REJECTED)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.quantity_reserved, 0)


class IndiaMapTests(TestCase):
    def setUp(self):
        self.admin = make_admin('map_admin')
        self.reporter = make_reporter('map_reporter')

    def test_india_map_page_loads(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:india_map'))
        self.assertEqual(resp.status_code, 200)

    def test_india_map_data_unauthenticated_redirects(self):
        resp = self.client.get(reverse('disaster_management:india_map_data'))
        self.assertIn(resp.status_code, [302, 403])

    def test_districts_ajax_returns_json(self):
        self.client.force_login(self.reporter)
        state = make_state('Ajax State', 'AX1')
        d = make_district(state, 'Ajax District')
        resp = self.client.get(
            reverse('disaster_management:districts_by_state'),
            {'state_id': state.id}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('districts', data)
        names = [di['name'] for di in data['districts']]
        self.assertIn('Ajax District', names)


class EvacuationTests(TestCase):
    def setUp(self):
        self.admin = make_admin('evac_admin')
        self.reporter = make_reporter('evac_reporter')

    def test_evacuation_list_loads(self):
        self.client.force_login(self.reporter)
        resp = self.client.get(reverse('disaster_management:evacuation_list'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_create_evacuation_plan(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:evacuation_create'), data={
            'title': 'Test Evac Plan',
            'description': 'Moving people to safe shelters.',
            'affected_population': '1000',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EvacuationPlan.objects.filter(title='Test Evac Plan').count(), 1)

    def test_admin_can_activate_draft_plan(self):
        plan = EvacuationPlan.objects.create(
            title='Draft Plan', status=EvacuationPlan.Status.DRAFT,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('disaster_management:evacuation_activate', args=[plan.id]))
        self.assertEqual(resp.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, EvacuationPlan.Status.ACTIVE)
        self.assertIsNotNone(plan.started_at)


class LocationSubscriptionTests(TestCase):
    def setUp(self):
        self.reporter = make_reporter('sub_reporter')
        self.admin = make_admin('sub_admin')
        self.state = make_state('Sub State', 'SB1')

    def test_subscribe_to_state(self):
        self.client.force_login(self.reporter)
        resp = self.client.post(
            reverse('disaster_management:subscribe_location'),
            data={'state_id': self.state.id, 'notify_in_app': 'on'}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            LocationSubscription.objects.filter(user=self.reporter, state=self.state).count(), 1
        )

    def test_cannot_duplicate_subscription(self):
        LocationSubscription.objects.create(user=self.reporter, state=self.state)
        self.client.force_login(self.reporter)
        resp = self.client.post(
            reverse('disaster_management:subscribe_location'),
            data={'state_id': self.state.id, 'notify_in_app': 'on'}
        )
        self.assertEqual(
            LocationSubscription.objects.filter(user=self.reporter, state=self.state).count(), 1
        )
