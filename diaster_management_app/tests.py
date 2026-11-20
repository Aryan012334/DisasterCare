from datetime import timedelta
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from django.utils import timezone

from .forms import (
    EmergencyIncidentReportForm,
    IncidentReportForm,
    ResponderProfileForm,
    UserRegistrationForm,
    UserUpdateForm,
)
from .models import (
    Incident,
    IncidentAssignment,
    IncidentFakeReport,
    IncidentFinalReview,
    IncidentFinalReviewMedia,
    IncidentStatusEnquiry,
    IncidentUpdate,
    NotificationLog,
    PasswordResetOTP,
    ResponderProfile,
    ResponderUpgradeRequest,
    UserProfile,
)


class NotificationFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="reporter_user",
            email="reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="responder_user",
            email="responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.responder,
            full_name="Responder User",
            phone_number="9999999999",
            skills="First Aid",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

    def _normal_incident_payload(self):
        return {
            "title": "Road blocked near market",
            "incident_type": Incident.IncidentType.ROAD_ACCIDENT,
            "severity": Incident.Severity.MEDIUM,
            "description": "Vehicle collision, road blocked, help needed.",
            "address": "Main Street Junction",
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "people_affected": 2,
            "contact_phone": "9000000000",
            "affected_radius_km": "1.0",
            "latitude": "12.972000",
            "longitude": "77.595000",
        }

    def test_normal_report_notifies_admin_and_nearest_responder_without_emergency_flag(self):
        self.client.force_login(self.reporter)
        response = self.client.post(reverse("disaster_management:incident_report"), data=self._normal_incident_payload())
        self.assertEqual(response.status_code, 302)

        incident = Incident.objects.order_by("-id").first()
        self.assertIsNotNone(incident)
        self.assertEqual(incident.report_mode, Incident.ReportMode.NORMAL)
        self.assertEqual(incident.assignments.count(), 0)

        self.assertTrue(
            NotificationLog.objects.filter(
                incident=incident,
                target_type=NotificationLog.TargetType.ADMIN,
                subject__startswith="New reported incident #",
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                incident=incident,
                user=self.responder,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject__startswith="Normal incident #",
            ).exists()
        )

        self.client.force_login(self.responder)
        feed = self.client.get(reverse("disaster_management:notification_feed"), {"since_id": 0})
        self.assertEqual(feed.status_code, 200)
        items = feed.json().get("items", [])
        responder_normal_items = [item for item in items if item["subject"].startswith("Normal incident #")]
        self.assertTrue(responder_normal_items)
        self.assertFalse(responder_normal_items[0]["is_new_emergency_alert"])
        self.assertFalse(responder_normal_items[0]["is_emergency"])

    def test_notification_feed_returns_latest_twenty_notifications(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Feed ordering test",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.LOW,
            description="Ordering test",
            address="Test Lane",
            people_affected=0,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
        )
        for index in range(25):
            NotificationLog.objects.create(
                incident=incident,
                user=self.admin,
                target_type=NotificationLog.TargetType.ADMIN,
                subject=f"Notice {index + 1}",
                message="Test",
                delivered=True,
            )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("disaster_management:notification_feed"), {"since_id": 0})
        self.assertEqual(response.status_code, 200)
        items = response.json().get("items", [])
        item_ids = [item["id"] for item in items]

        expected_ids = list(NotificationLog.objects.filter(user=self.admin).order_by("-id").values_list("id", flat=True)[:20])
        expected_ids.reverse()
        self.assertEqual(item_ids, expected_ids)

    def test_emergency_feed_flags_alert_for_admin_and_responder(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Emergency test",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.CRITICAL,
            description="Emergency fire test",
            address="Industrial area",
            people_affected=5,
            affected_radius_km=3.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.DISPATCHED,
        )
        admin_log = NotificationLog.objects.create(
            incident=incident,
            user=self.admin,
            target_type=NotificationLog.TargetType.ADMIN,
            subject=f"Emergency alert #{incident.id}",
            message="Emergency incident reported.",
            delivered=True,
        )
        responder_log = NotificationLog.objects.create(
            incident=incident,
            user=self.responder,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=f"New incident #{incident.id}",
            message="Please respond immediately.",
            delivered=True,
        )

        self.client.force_login(self.admin)
        admin_feed = self.client.get(reverse("disaster_management:notification_feed"), {"since_id": 0}).json()
        admin_item = next((item for item in admin_feed["items"] if item["id"] == admin_log.id), None)
        self.assertIsNotNone(admin_item)
        self.assertTrue(admin_item["is_new_emergency_alert"])

        self.client.force_login(self.responder)
        responder_feed = self.client.get(reverse("disaster_management:notification_feed"), {"since_id": 0}).json()
        responder_item = next((item for item in responder_feed["items"] if item["id"] == responder_log.id), None)
        self.assertIsNotNone(responder_item)
        self.assertTrue(responder_item["is_new_emergency_alert"])

    def test_reporter_messages_stay_plain_when_responder_accepts(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Reporter language test",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.ROAD_ACCIDENT,
            severity=Incident.Severity.MEDIUM,
            description="Responder acceptance text should stay plain.",
            address="Language lane",
            people_affected=1,
            affected_radius_km=1.0,
            latitude=12.971700,
            longitude=77.594700,
            status=Incident.Status.VERIFIED,
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=0.2,
        )

        self.client.force_login(self.responder)
        response = self.client.post(reverse("disaster_management:assignment_accept", args=[incident.id]))
        self.assertEqual(response.status_code, 302)

        reporter_log = NotificationLog.objects.filter(
            incident=incident,
            user=self.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Team accepted your report #{incident.id}",
        ).first()
        self.assertIsNotNone(reporter_log)
        self.assertIn("response team", reporter_log.message.lower())
        self.assertNotIn("responder", reporter_log.message.lower())

    def test_reject_incident_notifies_admin_team(self):
        extra_admin = get_user_model().objects.create_superuser(
            username="extra_admin",
            email="extra_admin@example.com",
            password="pass12345",
        )
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Admin rejection notification test",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.LOW,
            description="Verify admin notification fan-out.",
            address="Admin lane",
            people_affected=0,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.PENDING,
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("disaster_management:incident_reject", args=[incident.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            NotificationLog.objects.filter(
                incident=incident,
                user=extra_admin,
                target_type=NotificationLog.TargetType.ADMIN,
                subject=f"Incident #{incident.id} rejected",
            ).exists()
        )

    def test_reporter_safety_warning_hides_risk_scores(self):
        payload = self._normal_incident_payload()
        payload.update(
            {
                "incident_type": Incident.IncidentType.FIRE,
                "severity": Incident.Severity.CRITICAL,
                "people_affected": 20,
                "affected_radius_km": "3.0",
            }
        )

        self.client.force_login(self.reporter)
        response = self.client.post(reverse("disaster_management:incident_report"), data=payload)
        self.assertEqual(response.status_code, 302)

        incident = Incident.objects.order_by("-id").first()
        self.assertIsNotNone(incident)
        reporter_warning = NotificationLog.objects.filter(
            incident=incident,
            user=self.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject__startswith="Safety update near you:",
        ).first()
        self.assertIsNotNone(reporter_warning)
        self.assertNotIn("risk in next", reporter_warning.message.lower())

    def test_reporter_cannot_open_notification_page(self):
        self.client.force_login(self.reporter)
        response = self.client.get(reverse("disaster_management:notification_list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:dashboard_home"))


class AssignmentRedispatchTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="reporter_for_assignment",
            email="reporter_assign@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder_one = self._create_responder(
            username="responder_one",
            email="responder_one@example.com",
            latitude=12.971600,
            longitude=77.594600,
        )
        self.responder_two = self._create_responder(
            username="responder_two",
            email="responder_two@example.com",
            latitude=12.975000,
            longitude=77.598000,
        )

    def _create_responder(self, username, email, latitude, longitude):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=username,
            email=email,
            password="pass12345",
            is_active=True,
        )
        user.profile.role = UserProfile.Role.RESPONDER
        user.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        user.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=user,
            full_name=username,
            phone_number="9999999999",
            skills="First Aid",
            is_available=True,
            is_verified=True,
            latitude=latitude,
            longitude=longitude,
            last_checkin_at=timezone.now(),
        )
        return user

    def test_declined_assignment_is_reoffered_and_other_responder_can_accept(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Emergency with reassignment",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.CRITICAL,
            description="Responder declined this task.",
            address="Central point",
            people_affected=3,
            affected_radius_km=2.0,
            latitude=12.971800,
            longitude=77.594900,
            status=Incident.Status.DISPATCHED,
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.responder_one,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=0.3,
        )

        self.client.force_login(self.responder_one)
        decline_response = self.client.post(reverse("disaster_management:assignment_decline", args=[incident.id]))
        self.assertEqual(decline_response.status_code, 302)

        first_assignment = IncidentAssignment.objects.get(incident=incident, responder=self.responder_one)
        self.assertEqual(first_assignment.status, IncidentAssignment.AssignmentStatus.DECLINED)

        second_assignment = IncidentAssignment.objects.filter(
            incident=incident,
            responder=self.responder_two,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
        ).first()
        self.assertIsNotNone(second_assignment)

        self.client.force_login(self.responder_two)
        accept_response = self.client.post(reverse("disaster_management:assignment_accept", args=[incident.id]))
        self.assertEqual(accept_response.status_code, 302)

        second_assignment.refresh_from_db()
        self.assertEqual(second_assignment.status, IncidentAssignment.AssignmentStatus.ACCEPTED)

    def test_declined_assignment_cannot_be_accepted_again_by_same_responder(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Decline lock incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Responder should not re-accept after decline.",
            address="Central point",
            people_affected=2,
            affected_radius_km=2.0,
            latitude=12.971800,
            longitude=77.594900,
            status=Incident.Status.DISPATCHED,
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.responder_one,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=0.3,
        )

        self.client.force_login(self.responder_one)
        self.client.post(reverse("disaster_management:assignment_decline", args=[incident.id]))
        accept_response = self.client.post(reverse("disaster_management:assignment_accept", args=[incident.id]))
        self.assertEqual(accept_response.status_code, 302)

        assignment = IncidentAssignment.objects.get(incident=incident, responder=self.responder_one)
        self.assertEqual(assignment.status, IncidentAssignment.AssignmentStatus.DECLINED)

    def test_declined_assignment_cannot_be_declined_again_by_same_responder(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Decline lock repeat incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Responder should not re-decline after decline.",
            address="Central point",
            people_affected=2,
            affected_radius_km=2.0,
            latitude=12.971800,
            longitude=77.594900,
            status=Incident.Status.DISPATCHED,
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.responder_one,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=0.3,
        )

        self.client.force_login(self.responder_one)
        self.client.post(reverse("disaster_management:assignment_decline", args=[incident.id]))
        second_decline_response = self.client.post(reverse("disaster_management:assignment_decline", args=[incident.id]))
        self.assertEqual(second_decline_response.status_code, 302)

        assignment = IncidentAssignment.objects.get(incident=incident, responder=self.responder_one)
        self.assertEqual(assignment.status, IncidentAssignment.AssignmentStatus.DECLINED)


class ResponderCommitmentRulesTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="commit_reporter",
            email="commit_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="commit_responder",
            email="commit_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.responder,
            full_name="Commit Responder",
            phone_number="9999999999",
            skills="Field response",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

        self.committed_incident = self._create_incident("Committed assignment")
        self.waiting_incident = self._create_incident("Waiting assignment")
        IncidentAssignment.objects.create(
            incident=self.committed_incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            distance_km=0.4,
            accepted_at=timezone.now(),
        )
        self.waiting_assignment = IncidentAssignment.objects.create(
            incident=self.waiting_incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=1.1,
        )

    def _create_incident(self, title):
        return Incident.objects.create(
            reporter=self.reporter,
            title=title,
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Commitment flow test incident.",
            address="Responder zone",
            people_affected=2,
            affected_radius_km=2.0,
            latitude=12.972000,
            longitude=77.595000,
            status=Incident.Status.DISPATCHED,
        )

    def test_responder_cannot_accept_second_incident_while_committed(self):
        self.client.force_login(self.responder)
        response = self.client.post(reverse("disaster_management:assignment_accept", args=[self.waiting_incident.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:incident_detail", args=[self.committed_incident.id]))

        self.waiting_assignment.refresh_from_db()
        self.assertEqual(self.waiting_assignment.status, IncidentAssignment.AssignmentStatus.NOTIFIED)

    def test_responder_redirected_to_committed_incident_when_opening_other_incident(self):
        self.client.force_login(self.responder)
        response = self.client.get(reverse("disaster_management:incident_detail", args=[self.waiting_incident.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:incident_detail", args=[self.committed_incident.id]))

    def test_incident_list_shows_open_my_committed_incident_link(self):
        self.client.force_login(self.responder)
        response = self.client.get(reverse("disaster_management:incident_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open My Current Case")
        self.assertContains(response, reverse("disaster_management:incident_detail", args=[self.committed_incident.id]))
        self.assertContains(response, "Locked")


class FakeReportCommitmentAndClosureTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="fakeflow_admin",
            email="fakeflow_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="fakeflow_reporter",
            email="fakeflow_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="fakeflow_responder",
            email="fakeflow_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.responder,
            full_name="Fake Flow Responder",
            phone_number="9999999999",
            skills="Field",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

        self.incident = Incident.objects.create(
            reporter=self.reporter,
            title="Fake flow active incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Responder suspects fake report.",
            address="Flow lane",
            people_affected=1,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.ON_SCENE,
            first_response_at=timezone.now(),
        )
        self.active_assignment = IncidentAssignment.objects.create(
            incident=self.incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.2,
        )

        self.next_incident = Incident.objects.create(
            reporter=self.reporter,
            title="Next commit incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.MEDIUM,
            description="Next incident to accept.",
            address="Flow lane 2",
            people_affected=1,
            affected_radius_km=1.0,
            latitude=12.972000,
            longitude=77.595000,
            status=Incident.Status.DISPATCHED,
        )
        self.waiting_assignment = IncidentAssignment.objects.create(
            incident=self.next_incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            distance_km=0.5,
        )

    def test_fake_report_submission_releases_commitment_for_new_assignment(self):
        self.client.force_login(self.responder)
        submit_response = self.client.post(
            reverse("disaster_management:incident_fake_report_submit", args=[self.incident.id]),
            data={
                "reason": "Location has no signs of incident, appears fake.",
                "attachments": [
                    SimpleUploadedFile("fake-proof.jpg", b"proof-bytes", content_type="image/jpeg"),
                ],
            },
        )
        self.assertEqual(submit_response.status_code, 302)
        self.active_assignment.refresh_from_db()
        self.assertEqual(self.active_assignment.status, IncidentAssignment.AssignmentStatus.CANCELLED)
        self.assertTrue(
            IncidentFakeReport.objects.filter(
                incident=self.incident,
                responder=self.responder,
                review_status=IncidentFakeReport.ReviewStatus.PENDING,
            ).exists()
        )

        accept_response = self.client.post(reverse("disaster_management:assignment_accept", args=[self.next_incident.id]))
        self.assertEqual(accept_response.status_code, 302)
        self.waiting_assignment.refresh_from_db()
        self.assertEqual(self.waiting_assignment.status, IncidentAssignment.AssignmentStatus.ACCEPTED)

    def test_admin_fake_report_action_closes_incident_and_releases_assignments(self):
        fake_report = IncidentFakeReport.objects.create(
            incident=self.incident,
            responder=self.responder,
            reason="Proof submitted for verification.",
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("disaster_management:fake_report_action", args=[fake_report.id]),
            data={
                "admin_action": IncidentFakeReport.AdminAction.NO_ACTION,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.active_assignment.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.CLOSED)
        self.assertIsNotNone(self.incident.closed_at)
        self.assertEqual(self.active_assignment.status, IncidentAssignment.AssignmentStatus.CANCELLED)
        self.assertTrue(
            IncidentUpdate.objects.filter(
                incident=self.incident,
                status=Incident.Status.CLOSED,
                note__icontains="closed this incident",
            ).exists()
        )

    def test_fake_report_submission_requires_on_scene_status(self):
        self.incident.status = Incident.Status.STABILIZED
        self.incident.save(update_fields=["status", "updated_at"])

        self.client.force_login(self.responder)
        submit_response = self.client.post(
            reverse("disaster_management:incident_fake_report_submit", args=[self.incident.id]),
            data={
                "reason": "Looks fake after stabilization.",
                "attachments": [
                    SimpleUploadedFile("fake-proof.jpg", b"proof-bytes", content_type="image/jpeg"),
                ],
            },
        )
        self.assertEqual(submit_response.status_code, 302)
        self.assertFalse(
            IncidentFakeReport.objects.filter(
                incident=self.incident,
                responder=self.responder,
            ).exists()
        )

    def test_on_scene_incident_detail_shows_fake_review_below_status_update(self):
        self.client.force_login(self.responder)
        response = self.client.get(reverse("disaster_management:incident_detail", args=[self.incident.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        status_section_index = content.find("Responder Field Status Update")
        fake_review_index = content.find("Fake Incident Review")
        self.assertNotEqual(status_section_index, -1)
        self.assertNotEqual(fake_review_index, -1)
        self.assertLess(status_section_index, fake_review_index)


class OnSceneLocationValidationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="onscene_reporter",
            email="onscene_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="onscene_responder",
            email="onscene_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        self.responder_profile = ResponderProfile.objects.create(
            user=self.responder,
            full_name="On Scene Responder",
            phone_number="9999999999",
            skills="Field",
            is_available=True,
            is_verified=True,
            latitude=12.970000,
            longitude=77.590000,
            last_checkin_at=timezone.now(),
        )

        self.incident = Incident.objects.create(
            reporter=self.reporter,
            title="On scene location check incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Validate on-scene location check.",
            address="Validation street",
            people_affected=2,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.DISPATCHED,
        )
        IncidentAssignment.objects.create(
            incident=self.incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.5,
        )

    def test_on_scene_update_requires_live_location_payload(self):
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[self.incident.id]),
            data={"status": Incident.Status.ON_SCENE, "note": "Reached area."},
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.DISPATCHED)
        self.assertIsNone(self.incident.first_response_at)

    def test_on_scene_update_rejected_when_location_is_far(self):
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[self.incident.id]),
            data={
                "status": Incident.Status.ON_SCENE,
                "note": "Reached area.",
                "on_scene_latitude": "13.500000",
                "on_scene_longitude": "77.594600",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.DISPATCHED)
        self.assertIsNone(self.incident.first_response_at)

    def test_on_scene_update_allowed_when_location_is_near(self):
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[self.incident.id]),
            data={
                "status": Incident.Status.ON_SCENE,
                "note": "Reached exact location.",
                "on_scene_latitude": "12.971610",
                "on_scene_longitude": "77.594610",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.responder_profile.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.ON_SCENE)
        self.assertIsNotNone(self.incident.first_response_at)
        self.assertAlmostEqual(float(self.responder_profile.latitude), 12.971610, places=5)
        self.assertAlmostEqual(float(self.responder_profile.longitude), 77.594610, places=5)


class IncidentClosureWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="close_admin",
            email="close_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="close_reporter",
            email="close_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="close_responder",
            email="close_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        self.responder_profile = ResponderProfile.objects.create(
            user=self.responder,
            full_name="Close Responder",
            phone_number="9999999999",
            skills="Field work",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

    def _create_incident(self, status):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Closure workflow incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Closure workflow validation.",
            address="Workflow lane",
            people_affected=2,
            affected_radius_km=2.0,
            latitude=12.972000,
            longitude=77.595000,
            status=status,
            dispatched_at=timezone.now(),
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.4,
        )
        return incident

    def _attach_final_review_with_proof(self, incident, *, recommend_close):
        review = IncidentFinalReview.objects.create(
            incident=incident,
            responder=self.responder,
            summary="Site stabilized and residents moved to safe area.",
            verification_notes="Fire source controlled and perimeter checked.",
            recommend_close=recommend_close,
        )
        IncidentFinalReviewMedia.objects.create(
            final_review=review,
            file=SimpleUploadedFile("proof.jpg", b"proof-bytes", content_type="image/jpeg"),
            caption="Proof",
        )
        return review

    def test_admin_can_force_close_when_closure_requirements_are_not_met(self):
        incident = self._create_incident(status=Incident.Status.ON_SCENE)
        review = self._attach_final_review_with_proof(incident, recommend_close=True)

        self.client.force_login(self.admin)
        response = self.client.post(reverse("disaster_management:incident_close", args=[incident.id]))
        self.assertEqual(response.status_code, 302)

        incident.refresh_from_db()
        review.refresh_from_db()
        self.responder_profile.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.CLOSED)
        self.assertFalse(review.points_awarded)
        self.assertEqual(self.responder_profile.completed_incidents, 0)
        self.assertEqual(
            IncidentAssignment.objects.get(incident=incident, responder=self.responder).status,
            IncidentAssignment.AssignmentStatus.CANCELLED,
        )

    def test_admin_can_close_when_stabilized_final_review_is_closure_ready(self):
        incident = self._create_incident(status=Incident.Status.STABILIZED)
        review = self._attach_final_review_with_proof(incident, recommend_close=True)

        self.client.force_login(self.admin)
        response = self.client.post(reverse("disaster_management:incident_close", args=[incident.id]))
        self.assertEqual(response.status_code, 302)

        incident.refresh_from_db()
        review.refresh_from_db()
        self.responder_profile.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.CLOSED)
        self.assertTrue(review.points_awarded)
        self.assertEqual(self.responder_profile.completed_incidents, 1)

    def test_admin_force_close_finalizes_open_status_enquiry(self):
        incident = self._create_incident(status=Incident.Status.ON_SCENE)
        IncidentStatusEnquiry.objects.create(
            incident=incident,
            requested_by=self.admin,
            target_responder=self.responder,
            request_note="Need update",
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("disaster_management:incident_close", args=[incident.id]))
        self.assertEqual(response.status_code, 302)

        incident.refresh_from_db()
        enquiry = IncidentStatusEnquiry.objects.get(incident=incident)
        self.assertEqual(incident.status, Incident.Status.CLOSED)
        self.assertEqual(enquiry.status, IncidentStatusEnquiry.WorkflowStatus.FINALIZED)

    def test_responder_final_review_requires_stabilized_or_escalated_status(self):
        incident = self._create_incident(status=Incident.Status.ON_SCENE)
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_final_review_submit", args=[incident.id]),
            data={
                "summary": "Final review summary with enough details.",
                "verification_notes": "Checked area and validated condition.",
                "recommend_close": "on",
                "attachments": [
                    SimpleUploadedFile("proof.jpg", b"proof-bytes", content_type="image/jpeg"),
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(IncidentFinalReview.objects.filter(incident=incident).exists())

    def test_responder_can_submit_final_review_when_status_is_escalated(self):
        incident = self._create_incident(status=Incident.Status.ESCALATED)
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_final_review_submit", args=[incident.id]),
            data={
                "summary": "Escalation summary with evidence details.",
                "verification_notes": "Additional teams required on the site.",
                "recommend_close": "on",
                "attachments": [
                    SimpleUploadedFile("proof.jpg", b"proof-bytes", content_type="image/jpeg"),
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        review = IncidentFinalReview.objects.filter(incident=incident).first()
        self.assertIsNotNone(review)
        self.assertFalse(review.recommend_close)


class IncidentHistoryViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="history_admin",
            email="history_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="history_reporter",
            email="history_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.other_reporter = user_model.objects.create_user(
            username="history_other_reporter",
            email="history_other_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.other_reporter.profile.role = UserProfile.Role.REPORTER
        self.other_reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.other_reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="history_responder",
            email="history_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.responder,
            full_name="History Responder",
            phone_number="9999999999",
            skills="Support",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

        self.closed_incident = Incident.objects.create(
            reporter=self.reporter,
            title="Closed history record",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.LOW,
            description="Closed incident history entry",
            address="History lane",
            people_affected=0,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.CLOSED,
            closed_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=self.closed_incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.5,
        )

        self.rejected_incident = Incident.objects.create(
            reporter=self.other_reporter,
            title="Rejected history record",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Rejected incident history entry",
            address="History road",
            people_affected=1,
            affected_radius_km=2.0,
            latitude=12.980000,
            longitude=77.600000,
            status=Incident.Status.REJECTED,
        )

    def test_admin_can_view_all_history_records(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("disaster_management:incident_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Closed history record")
        self.assertContains(response, "Rejected history record")

    def test_reporter_history_view_shows_only_own_records(self):
        self.client.force_login(self.reporter)
        response = self.client.get(reverse("disaster_management:incident_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Closed history record")
        self.assertNotContains(response, "Rejected history record")

    def test_responder_history_view_shows_assigned_records_only(self):
        self.client.force_login(self.responder)
        response = self.client.get(reverse("disaster_management:incident_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Closed history record")
        self.assertNotContains(response, "Rejected history record")


class EscalationDispatchTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="escalate_reporter",
            email="escalate_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.primary_responder = self._create_responder(
            username="escalate_primary",
            email="escalate_primary@example.com",
            latitude=12.971600,
            longitude=77.594600,
        )
        self.support_responder = self._create_responder(
            username="escalate_support",
            email="escalate_support@example.com",
            latitude=13.000000,
            longitude=77.594600,
        )
        self.far_responder = self._create_responder(
            username="escalate_far",
            email="escalate_far@example.com",
            latitude=13.400000,
            longitude=77.594600,
        )

    def _create_responder(self, username, email, latitude, longitude):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=username,
            email=email,
            password="pass12345",
            is_active=True,
        )
        user.profile.role = UserProfile.Role.RESPONDER
        user.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        user.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=user,
            full_name=username,
            phone_number="9999999999",
            skills="Escalation support",
            is_available=True,
            is_verified=True,
            latitude=latitude,
            longitude=longitude,
            last_checkin_at=timezone.now(),
        )
        return user

    def test_escalated_status_alerts_responders_within_thirty_km(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Escalation incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Escalation support needed.",
            address="Escalation zone",
            people_affected=3,
            affected_radius_km=2.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.ON_SCENE,
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.primary_responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.1,
        )

        self.client.force_login(self.primary_responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[incident.id]),
            data={"status": Incident.Status.ESCALATED, "note": "Situation escalated with heavy spread."},
        )
        self.assertEqual(response.status_code, 302)

        self.assertTrue(
            IncidentAssignment.objects.filter(
                incident=incident,
                responder=self.support_responder,
                status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            ).exists()
        )
        self.assertFalse(
            IncidentAssignment.objects.filter(
                incident=incident,
                responder=self.far_responder,
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                incident=incident,
                user=self.support_responder,
                subject=f"Escalated incident #{incident.id}",
            ).exists()
        )

    def test_escalation_does_not_reopen_previously_declined_assignment(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Escalation declined lock incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Declined responder should stay declined.",
            address="Escalation zone",
            people_affected=3,
            affected_radius_km=2.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.ON_SCENE,
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.primary_responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.1,
        )
        support_assignment = IncidentAssignment.objects.create(
            incident=incident,
            responder=self.support_responder,
            status=IncidentAssignment.AssignmentStatus.DECLINED,
            distance_km=2.0,
        )

        self.client.force_login(self.primary_responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[incident.id]),
            data={"status": Incident.Status.ESCALATED, "note": "Situation escalated with heavy spread."},
        )
        self.assertEqual(response.status_code, 302)

        support_assignment.refresh_from_db()
        self.assertEqual(support_assignment.status, IncidentAssignment.AssignmentStatus.DECLINED)
        self.assertFalse(
            NotificationLog.objects.filter(
                incident=incident,
                user=self.support_responder,
                subject=f"Escalated incident #{incident.id}",
            ).exists()
        )

    def test_responder_can_move_status_from_escalated_to_stabilized(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Escalation rollback incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Control improved after escalation.",
            address="Escalation zone",
            people_affected=2,
            affected_radius_km=1.5,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.ESCALATED,
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.primary_responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.1,
        )

        self.client.force_login(self.primary_responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[incident.id]),
            data={"status": Incident.Status.STABILIZED, "note": "Situation stabilized with support on site."},
        )
        self.assertEqual(response.status_code, 302)
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.STABILIZED)

    def test_incident_detail_shows_only_available_status_action_buttons(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Escalation button visibility incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Only stabilized action should be visible.",
            address="Escalation zone",
            people_affected=2,
            affected_radius_km=1.5,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.ESCALATED,
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.primary_responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.1,
        )

        self.client.force_login(self.primary_responder)
        response = self.client.get(reverse("disaster_management:incident_detail", args=[incident.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="stabilized"')
        self.assertNotContains(response, 'value="on_scene"')
        self.assertNotContains(response, 'value="escalated"')

    def test_stabilized_incident_detail_hides_responder_status_update_section(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Final review ordering incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Final review should appear earlier once stabilized.",
            address="Escalation zone",
            people_affected=2,
            affected_radius_km=1.5,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.STABILIZED,
            first_response_at=timezone.now(),
        )
        IncidentAssignment.objects.create(
            incident=incident,
            responder=self.primary_responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.1,
        )

        self.client.force_login(self.primary_responder)
        response = self.client.get(reverse("disaster_management:incident_detail", args=[incident.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final Field Review")
        self.assertNotContains(response, "Responder Field Status Update")


class EmergencyFallbackDispatchTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="fallback_admin",
            email="fallback_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="fallback_reporter",
            email="fallback_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.fallback_responder = user_model.objects.create_user(
            username="fallback_responder",
            email="fallback_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.fallback_responder.profile.role = UserProfile.Role.RESPONDER
        self.fallback_responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.fallback_responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.fallback_responder,
            full_name="Fallback Responder",
            phone_number="9999999999",
            skills="Fallback",
            is_available=True,
            is_verified=True,
            latitude=13.500000,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

    def test_emergency_verify_uses_closest_fallback_when_no_nearby_responder(self):
        incident = Incident.objects.create(
            reporter=self.reporter,
            title="Emergency fallback incident",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.CRITICAL,
            description="Emergency with no nearby responders.",
            address="Fallback test location",
            people_affected=4,
            affected_radius_km=2.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.PENDING,
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("disaster_management:incident_verify", args=[incident.id]))
        self.assertEqual(response.status_code, 302)

        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.DISPATCHED)
        self.assertTrue(
            IncidentAssignment.objects.filter(
                incident=incident,
                responder=self.fallback_responder,
                status=IncidentAssignment.AssignmentStatus.NOTIFIED,
            ).exists()
        )
        fallback_notification = NotificationLog.objects.filter(
            incident=incident,
            user=self.fallback_responder,
            subject=f"New incident #{incident.id}",
        ).first()
        self.assertIsNotNone(fallback_notification)
        self.assertIn("closest available responder", fallback_notification.message.lower())


class IncidentStatusEnquiryWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="enquiry_admin",
            email="enquiry_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="enquiry_reporter",
            email="enquiry_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="enquiry_responder",
            email="enquiry_responder@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])
        ResponderProfile.objects.create(
            user=self.responder,
            full_name="Enquiry Responder",
            phone_number="9999999999",
            skills="Support",
            is_available=True,
            is_verified=True,
            latitude=12.971600,
            longitude=77.594600,
            last_checkin_at=timezone.now(),
        )

        self.incident = Incident.objects.create(
            reporter=self.reporter,
            title="Stale incident for enquiry",
            report_mode=Incident.ReportMode.EMERGENCY,
            incident_type=Incident.IncidentType.FIRE,
            severity=Incident.Severity.HIGH,
            description="Need stale enquiry workflow.",
            address="Enquiry lane",
            people_affected=2,
            affected_radius_km=2.0,
            latitude=12.971600,
            longitude=77.594600,
            status=Incident.Status.DISPATCHED,
        )
        IncidentAssignment.objects.create(
            incident=self.incident,
            responder=self.responder,
            status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            accepted_at=timezone.now(),
            distance_km=0.2,
        )
        old_time = timezone.now() - timedelta(days=4)
        seed_update = IncidentUpdate.objects.create(
            incident=self.incident,
            actor=self.admin,
            status=self.incident.status,
            note="Old update for stale workflow.",
        )
        IncidentUpdate.objects.filter(id=seed_update.id).update(created_at=old_time, updated_at=old_time)
        Incident.objects.filter(id=self.incident.id).update(updated_at=old_time)
        self.incident.refresh_from_db()

    def test_admin_request_enquiry_notifies_responder(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("disaster_management:incident_status_enquiry_request", args=[self.incident.id]),
            data={"request_note": "Please provide current field status."},
        )
        self.assertEqual(response.status_code, 302)
        enquiry = IncidentStatusEnquiry.objects.get(incident=self.incident)
        self.assertEqual(enquiry.status, IncidentStatusEnquiry.WorkflowStatus.REQUESTED)
        self.assertEqual(enquiry.target_responder_id, self.responder.id)
        self.assertTrue(
            NotificationLog.objects.filter(
                incident=self.incident,
                user=self.responder,
                subject=f"Status enquiry requested for incident #{self.incident.id}",
            ).exists()
        )

    def test_stale_incident_blocks_regular_status_update_without_admin_enquiry(self):
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_update", args=[self.incident.id]),
            data={"status": Incident.Status.ON_SCENE, "note": "Reached location and checking."},
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.DISPATCHED)

    def test_responder_can_submit_status_after_admin_enquiry(self):
        IncidentStatusEnquiry.objects.create(
            incident=self.incident,
            requested_by=self.admin,
            target_responder=self.responder,
            request_note="Need current status now.",
            requested_at=timezone.now(),
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
        )
        self.client.force_login(self.responder)
        response = self.client.post(
            reverse("disaster_management:incident_status_enquiry_response", args=[self.incident.id]),
            data={
                "current_status": Incident.Status.STABILIZED,
                "responder_note": "Area stabilized and follow-up checks done.",
                "recommend_finalize": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        enquiry = IncidentStatusEnquiry.objects.get(incident=self.incident)
        self.assertEqual(self.incident.status, Incident.Status.STABILIZED)
        self.assertEqual(enquiry.status, IncidentStatusEnquiry.WorkflowStatus.RESPONDED)
        self.assertTrue(enquiry.responder_recommend_finalize)

    def test_admin_can_take_warning_action_after_one_day_without_response(self):
        enquiry = IncidentStatusEnquiry.objects.create(
            incident=self.incident,
            requested_by=self.admin,
            target_responder=self.responder,
            request_note="Pending response",
            requested_at=timezone.now() - timedelta(days=2),
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("disaster_management:incident_status_enquiry_overdue_action", args=[self.incident.id]),
            data={"action": "warning", "admin_note": "Delayed update response."},
        )
        self.assertEqual(response.status_code, 302)
        enquiry.refresh_from_db()
        self.responder.profile.refresh_from_db()
        self.assertEqual(enquiry.overdue_action, IncidentStatusEnquiry.OverdueAction.WARNING)
        self.assertEqual(self.responder.profile.warning_count, 1)


class UploadValidationTests(TestCase):
    def _incident_payload(self):
        return {
            "title": "Validation test incident",
            "incident_type": Incident.IncidentType.FIRE,
            "severity": Incident.Severity.HIGH,
            "description": "Smoke seen near building",
            "address": "Validation road",
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "people_affected": 3,
            "contact_phone": "9999999999",
            "affected_radius_km": "1.5",
            "latitude": "12.971600",
            "longitude": "77.594600",
        }

    def test_incident_form_rejects_disallowed_attachment_extension(self):
        payload = self._incident_payload()
        suspicious_file = SimpleUploadedFile("malware.exe", b"MZ", content_type="application/octet-stream")
        form = IncidentReportForm(data=payload, files=MultiValueDict({"attachments": [suspicious_file]}))
        self.assertFalse(form.is_valid())
        self.assertIn("attachments", form.errors)

    def test_incident_form_rejects_invalid_attachment_content_type(self):
        payload = self._incident_payload()
        fake_image = SimpleUploadedFile("photo.jpg", b"not-an-image", content_type="text/plain")
        form = IncidentReportForm(data=payload, files=MultiValueDict({"attachments": [fake_image]}))
        self.assertFalse(form.is_valid())
        self.assertIn("attachments", form.errors)

    def test_registration_form_rejects_non_image_profile_photo(self):
        payload = {
            "first_name": "Jane",
            "last_name": "Doe",
            "username": "jane_upload_check",
            "email": "jane_upload_check@example.com",
            "password1": "Pass12345!xyz",
            "password2": "Pass12345!xyz",
            "role": UserProfile.Role.REPORTER,
        }
        bad_photo = SimpleUploadedFile("avatar.txt", b"hello", content_type="text/plain")
        form = UserRegistrationForm(data=payload, files={"profile_photo": bad_photo})
        self.assertFalse(form.is_valid())
        self.assertIn("profile_photo", form.errors)

    def test_registration_form_rejects_oversized_profile_photo(self):
        payload = {
            "first_name": "Jane",
            "last_name": "Doe",
            "username": "jane_large_photo",
            "email": "jane_large_photo@example.com",
            "password1": "Pass12345!xyz",
            "password2": "Pass12345!xyz",
            "role": UserProfile.Role.REPORTER,
        }
        large_photo = SimpleUploadedFile(
            "avatar.jpg",
            b"a" * ((3 * 1024 * 1024) + 1),
            content_type="image/jpeg",
        )
        form = UserRegistrationForm(data=payload, files={"profile_photo": large_photo})
        self.assertFalse(form.is_valid())
        self.assertIn("profile_photo", form.errors)

    def test_user_update_form_rejects_numbers_in_first_name(self):
        user = get_user_model().objects.create_user(
            username="name_update_user",
            email="name_update_user@example.com",
            password="pass12345",
        )
        form = UserUpdateForm(
            data={
                "first_name": "Jane1",
                "last_name": "Doe",
                "email": "name_update_user@example.com",
            },
            instance=user,
            user_instance=user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("first_name", form.errors)
        self.assertIn("cannot contain numbers", form.errors["first_name"][0])

    def test_responder_profile_form_rejects_numbers_in_full_name(self):
        form = ResponderProfileForm(
            data={
                "full_name": "Responder 1",
                "phone_number": "9999999999",
                "skills": "Field response",
                "is_available": "on",
                "latitude": "12.971600",
                "longitude": "77.594600",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)
        self.assertIn("cannot contain numbers", form.errors["full_name"][0])

    def test_emergency_form_defaults_optional_people_and_radius(self):
        payload = {
            "title": "Critical smoke near warehouse",
            "incident_type": Incident.IncidentType.FIRE,
            "severity": Incident.Severity.CRITICAL,
            "description": "Heavy smoke, immediate support needed.",
            "address": "",
            "contact_phone": "",
            "latitude": "12.971600",
            "longitude": "77.594600",
            "confirm_emergency_policy": "on",
        }
        form = EmergencyIncidentReportForm(data=payload)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["people_affected"], 0)
        self.assertEqual(form.cleaned_data["affected_radius_km"], 3.0)


class FakeReportModerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="admin_moderation",
            email="admin_moderation@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="reporter_moderation",
            email="reporter_moderation@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.responder = user_model.objects.create_user(
            username="responder_moderation",
            email="responder_moderation@example.com",
            password="pass12345",
            is_active=True,
        )
        self.responder.profile.role = UserProfile.Role.RESPONDER
        self.responder.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.responder.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.incident = Incident.objects.create(
            reporter=self.reporter,
            title="Moderation incident",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.MEDIUM,
            description="Moderation flow test",
            address="Test place",
            people_affected=1,
            affected_radius_km=1.0,
            latitude=12.971600,
            longitude=77.594600,
        )
        self.fake_report = IncidentFakeReport.objects.create(
            incident=self.incident,
            responder=self.responder,
            reason="Evidence suggests this report might be fake.",
        )
        self.extra_incident = Incident.objects.create(
            reporter=self.reporter,
            title="Reporter extra incident",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.LOW,
            description="Extra incident from same reporter.",
            address="Extra place",
            people_affected=0,
            affected_radius_km=1.0,
            latitude=12.970000,
            longitude=77.590000,
        )
        self.other_reporter = user_model.objects.create_user(
            username="other_reporter_moderation",
            email="other_reporter_moderation@example.com",
            password="pass12345",
            is_active=True,
        )
        self.other_reporter.profile.role = UserProfile.Role.REPORTER
        self.other_reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.other_reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])
        self.other_incident = Incident.objects.create(
            reporter=self.other_reporter,
            title="Other reporter incident",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.LOW,
            description="Should not be deleted.",
            address="Other place",
            people_affected=0,
            affected_radius_km=1.0,
            latitude=12.965000,
            longitude=77.580000,
        )

    def test_warning_action_is_idempotent_for_same_fake_report(self):
        self.client.force_login(self.admin)
        action_url = reverse("disaster_management:fake_report_action", args=[self.fake_report.id])

        response_one = self.client.post(action_url, {"admin_action": IncidentFakeReport.AdminAction.WARNING})
        self.assertEqual(response_one.status_code, 302)
        self.reporter.profile.refresh_from_db()
        self.assertEqual(self.reporter.profile.warning_count, 1)

        response_two = self.client.post(action_url, {"admin_action": IncidentFakeReport.AdminAction.WARNING})
        self.assertEqual(response_two.status_code, 302)
        self.reporter.profile.refresh_from_db()
        self.assertEqual(self.reporter.profile.warning_count, 1)

    def test_queue_and_history_pages_split_pending_and_reviewed_reports(self):
        reviewed_incident = Incident.objects.create(
            reporter=self.reporter,
            title="Reviewed fake-report incident",
            report_mode=Incident.ReportMode.NORMAL,
            incident_type=Incident.IncidentType.OTHER,
            severity=Incident.Severity.MEDIUM,
            description="History entry incident.",
            address="History place",
            people_affected=1,
            affected_radius_km=1.0,
            latitude=12.971000,
            longitude=77.594000,
        )
        reviewed_report = IncidentFakeReport.objects.create(
            incident=reviewed_incident,
            responder=self.responder,
            reason="Reviewed-only fake reason marker",
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.NO_ACTION,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )

        self.client.force_login(self.admin)
        queue_response = self.client.get(reverse("disaster_management:fake_reports_review"))
        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, "Evidence suggests this report might be fake.")
        self.assertNotContains(queue_response, reviewed_report.reason)

        history_response = self.client.get(reverse("disaster_management:fake_reports_history"))
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, reviewed_report.reason)
        self.assertNotContains(history_response, "Evidence suggests this report might be fake.")

    def test_action_can_redirect_back_to_history_page(self):
        self.client.force_login(self.admin)
        action_url = reverse("disaster_management:fake_report_action", args=[self.fake_report.id])
        response = self.client.post(
            action_url,
            {
                "admin_action": IncidentFakeReport.AdminAction.NO_ACTION,
                "return_to": "history",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:fake_reports_history"))

    def test_block_action_can_delete_all_reported_incidents_of_reporter(self):
        self.client.force_login(self.admin)
        action_url = reverse("disaster_management:fake_report_action", args=[self.fake_report.id])
        response = self.client.post(
            action_url,
            {
                "admin_action": IncidentFakeReport.AdminAction.BLOCK,
                "admin_note": "Confirmed repeated fake reports.",
                "purge_reporter_incidents": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.reporter.refresh_from_db()
        self.reporter.profile.refresh_from_db()
        self.assertFalse(self.reporter.is_active)
        self.assertTrue(self.reporter.profile.is_blocked)
        self.assertEqual(Incident.objects.filter(reporter=self.reporter).count(), 0)
        self.assertTrue(Incident.objects.filter(id=self.other_incident.id).exists())


class IncidentWorkflowLimitsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="report_limit_reporter",
            email="report_limit_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

    def _payload(self):
        return {
            "title": "Rate limit test incident",
            "incident_type": Incident.IncidentType.OTHER,
            "severity": Incident.Severity.MEDIUM,
            "description": "Rate limit validation incident.",
            "address": "Limit lane",
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "people_affected": 1,
            "contact_phone": "9000000000",
            "affected_radius_km": "1.0",
            "latitude": "12.971600",
            "longitude": "77.594600",
        }

    def test_daily_limit_blocks_additional_incident_submission(self):
        for idx in range(8):
            Incident.objects.create(
                reporter=self.reporter,
                title=f"Existing {idx}",
                report_mode=Incident.ReportMode.NORMAL,
                incident_type=Incident.IncidentType.OTHER,
                severity=Incident.Severity.LOW,
                description="Existing incident",
                address="Somewhere",
                people_affected=0,
                affected_radius_km=1.0,
                latitude=12.971600,
                longitude=77.594600,
            )

        self.client.force_login(self.reporter)
        response = self.client.post(reverse("disaster_management:incident_report"), data=self._payload())
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Daily incident limit reached", status_code=429)
        self.assertEqual(Incident.objects.filter(reporter=self.reporter).count(), 8)


class ResponderUpgradeFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="upgrade_admin",
            email="upgrade_admin@example.com",
            password="pass12345",
        )
        self.reporter = user_model.objects.create_user(
            username="upgrade_reporter",
            email="upgrade_reporter@example.com",
            password="pass12345",
            is_active=True,
        )
        self.reporter.profile.role = UserProfile.Role.REPORTER
        self.reporter.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

    def test_reporter_upgrade_request_and_admin_approval_flow(self):
        self.client.force_login(self.reporter)
        request_response = self.client.post(
            reverse("disaster_management:profile_edit"),
            data={
                "form_type": "upgrade_request",
                "full_name": "Upgrade Reporter",
                "phone_number": "9999999999",
                "skills": "First aid, evacuation support",
                "reason": "Available for field response.",
            },
        )
        self.assertEqual(request_response.status_code, 302)

        upgrade_request = ResponderUpgradeRequest.objects.get(user=self.reporter)
        self.assertEqual(upgrade_request.status, ResponderUpgradeRequest.Status.PENDING)

        self.client.force_login(self.admin)
        approve_response = self.client.post(
            reverse("disaster_management:approve_responder_upgrade", args=[upgrade_request.id]),
            data={"admin_note": "Validated and approved."},
        )
        self.assertEqual(approve_response.status_code, 302)

        self.reporter.refresh_from_db()
        upgrade_request.refresh_from_db()
        self.assertEqual(self.reporter.profile.role, UserProfile.Role.RESPONDER)
        self.assertEqual(upgrade_request.status, ResponderUpgradeRequest.Status.APPROVED)
        self.assertTrue(hasattr(self.reporter, "responder_profile"))

    def test_reporter_cannot_create_duplicate_pending_upgrade_requests(self):
        ResponderUpgradeRequest.objects.create(
            user=self.reporter,
            full_name="Upgrade Reporter",
            phone_number="9999999999",
            skills="First aid",
            reason="Already requested",
        )

        self.client.force_login(self.reporter)
        response = self.client.post(
            reverse("disaster_management:profile_edit"),
            data={
                "form_type": "upgrade_request",
                "full_name": "Upgrade Reporter",
                "phone_number": "9999999999",
                "skills": "First aid",
                "reason": "Trying duplicate request.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ResponderUpgradeRequest.objects.filter(user=self.reporter, status=ResponderUpgradeRequest.Status.PENDING).count(),
            1,
        )

    def test_upgrade_request_rejects_numbers_in_full_name(self):
        self.client.force_login(self.reporter)
        response = self.client.post(
            reverse("disaster_management:profile_edit"),
            data={
                "form_type": "upgrade_request",
                "full_name": "Upgrade Reporter 2",
                "phone_number": "9999999999",
                "skills": "First aid",
                "reason": "Available for field response.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Full name cannot contain numbers.")
        self.assertFalse(ResponderUpgradeRequest.objects.filter(user=self.reporter).exists())


class ProfilePasswordChangeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="password_user",
            email="password_user@example.com",
            password="pass12345",
            is_active=True,
        )
        self.user.profile.role = UserProfile.Role.REPORTER
        self.user.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.user.profile.save(update_fields=["role", "verification_status", "updated_at"])

    def test_non_admin_user_can_change_password_from_profile(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("disaster_management:profile_edit"),
            data={
                "form_type": "password_change",
                "old_password": "pass12345",
                "new_password1": "NewPass12345!a",
                "new_password2": "NewPass12345!a",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass12345!a"))


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_RESET_OTP_EXPIRY_SECONDS=600,
    PASSWORD_RESET_OTP_MAX_ATTEMPTS=5,
    PASSWORD_RESET_OTP_RESEND_COOLDOWN_SECONDS=60,
    PASSWORD_RESET_VERIFIED_SESSION_SECONDS=900,
)
class PasswordResetOTPFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="otp_reporter",
            email="otp_reporter@example.com",
            password="OldPass12345!a",
            is_active=True,
        )
        self.user.profile.role = UserProfile.Role.REPORTER
        self.user.profile.verification_status = UserProfile.VerificationStatus.APPROVED
        self.user.profile.save(update_fields=["role", "verification_status", "updated_at"])

    def _request_otp(self):
        return self.client.post(
            reverse("disaster_management:password_reset"),
            data={"email": self.user.email},
        )

    def _extract_latest_code(self):
        self.assertTrue(mail.outbox)
        body = mail.outbox[-1].body
        match = re.search(r"\b(\d{6})\b", body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_full_otp_password_reset_flow(self):
        response = self._request_otp()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user, is_used=False).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

        otp_code = self._extract_latest_code()
        verify_response = self.client.post(
            reverse("disaster_management:password_reset_verify"),
            data={"email": self.user.email, "otp": otp_code},
        )
        self.assertEqual(verify_response.status_code, 302)
        self.assertEqual(verify_response.url, reverse("disaster_management:password_reset_new_password"))

        new_password = "VeryNewPass12345!a"
        reset_response = self.client.post(
            reverse("disaster_management:password_reset_new_password"),
            data={
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        self.assertEqual(reset_response.status_code, 302)
        self.assertEqual(reset_response.url, reverse("disaster_management:password_reset_complete"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.assertNotIn("password_reset_user_id", self.client.session)

    def test_wrong_otp_locks_after_max_attempts(self):
        self._request_otp()

        for _ in range(5):
            wrong_response = self.client.post(
                reverse("disaster_management:password_reset_verify"),
                data={"email": self.user.email, "otp": "000000"},
            )
            self.assertEqual(wrong_response.status_code, 400)

        challenge = PasswordResetOTP.objects.filter(user=self.user, is_used=False).first()
        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.attempts_remaining, 0)

        blocked_response = self.client.post(
            reverse("disaster_management:password_reset_verify"),
            data={"email": self.user.email, "otp": "000000"},
        )
        self.assertEqual(blocked_response.status_code, 429)

    def test_resend_respects_cooldown_then_sends_new_code(self):
        self._request_otp()
        challenge = PasswordResetOTP.objects.filter(user=self.user, is_used=False).first()
        self.assertIsNotNone(challenge)
        initial_hash = challenge.otp_hash
        initial_expiry = challenge.expires_at

        immediate_resend = self.client.post(
            reverse("disaster_management:password_reset_resend"),
            data={"email": self.user.email},
        )
        self.assertEqual(immediate_resend.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

        challenge.last_sent_at = timezone.now() - timedelta(seconds=120)
        challenge.save(update_fields=["last_sent_at", "updated_at"])

        delayed_resend = self.client.post(
            reverse("disaster_management:password_reset_resend"),
            data={"email": self.user.email},
        )
        self.assertEqual(delayed_resend.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)

        challenge.refresh_from_db()
        self.assertEqual(challenge.send_count, 2)
        self.assertNotEqual(challenge.otp_hash, initial_hash)
        self.assertGreater(challenge.expires_at, initial_expiry)

    def test_expired_otp_is_rejected(self):
        self._request_otp()
        otp_code = self._extract_latest_code()
        challenge = PasswordResetOTP.objects.filter(user=self.user, is_used=False).first()
        self.assertIsNotNone(challenge)
        challenge.expires_at = timezone.now() - timedelta(seconds=1)
        challenge.save(update_fields=["expires_at", "updated_at"])

        verify_response = self.client.post(
            reverse("disaster_management:password_reset_verify"),
            data={"email": self.user.email, "otp": otp_code},
        )
        self.assertEqual(verify_response.status_code, 400)
        self.assertNotIn("password_reset_user_id", self.client.session)

    def test_reset_password_page_requires_verified_otp(self):
        response = self.client.get(reverse("disaster_management:password_reset_new_password"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:password_reset"))

    def test_unknown_email_does_not_create_password_reset_challenge(self):
        response = self.client.post(
            reverse("disaster_management:password_reset"),
            data={"email": "unknown_person@example.com"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No account is linked to this email address.", status_code=400)
        self.assertEqual(PasswordResetOTP.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


class RegistrationApprovalFlowTests(TestCase):
    def _registration_payload(self, *, username, email, role):
        return {
            "first_name": "Test",
            "last_name": "User",
            "username": username,
            "email": email,
            "role": role,
            "password1": "TestPass12345!a",
            "password2": "TestPass12345!a",
        }

    def test_reporter_signup_is_active_and_auto_approved(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data=self._registration_payload(
                username="auto_reporter",
                email="auto_reporter@example.com",
                role=UserProfile.Role.REPORTER,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:login"))

        user = get_user_model().objects.get(username="auto_reporter")
        self.assertTrue(user.is_active)
        self.assertEqual(user.profile.role, UserProfile.Role.REPORTER)
        self.assertEqual(user.profile.verification_status, UserProfile.VerificationStatus.APPROVED)
        self.assertIsNotNone(user.profile.verified_at)

    def test_register_rejects_whitespace_first_name(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data={
                **self._registration_payload(
                    username="first_name_space",
                    email="first_name_space@example.com",
                    role=UserProfile.Role.REPORTER,
                ),
                "first_name": "   ",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "This field is required.", status_code=400)
        self.assertFalse(get_user_model().objects.filter(username="first_name_space").exists())

    def test_register_rejects_numbers_in_first_name(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data={
                **self._registration_payload(
                    username="numeric_first_name",
                    email="numeric_first_name@example.com",
                    role=UserProfile.Role.REPORTER,
                ),
                "first_name": "Test1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "First name cannot contain numbers.", status_code=400)
        self.assertFalse(get_user_model().objects.filter(username="numeric_first_name").exists())

    def test_register_rejects_numbers_in_last_name(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data={
                **self._registration_payload(
                    username="numeric_last_name",
                    email="numeric_last_name@example.com",
                    role=UserProfile.Role.REPORTER,
                ),
                "last_name": "User2",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Last name cannot contain numbers.", status_code=400)
        self.assertFalse(get_user_model().objects.filter(username="numeric_last_name").exists())

    def test_register_rejects_duplicate_email_case_insensitive(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="existing_email_user",
            email="duplicate_mail@example.com",
            password="pass12345",
            is_active=True,
        )

        response = self.client.post(
            reverse("disaster_management:register"),
            data=self._registration_payload(
                username="duplicate_email_attempt",
                email="DUPLICATE_MAIL@example.com",
                role=UserProfile.Role.REPORTER,
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "This email is already registered.", status_code=400)
        self.assertFalse(user_model.objects.filter(username="duplicate_email_attempt").exists())

    def test_register_rejects_password_mismatch(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data={
                **self._registration_payload(
                    username="mismatch_pass_user",
                    email="mismatch_pass_user@example.com",
                    role=UserProfile.Role.REPORTER,
                ),
                "password2": "DifferentPass12345!a",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="mismatch_pass_user").exists())

    def test_register_reporter_route_ignores_tampered_role_payload(self):
        response = self.client.post(
            reverse("disaster_management:register_reporter"),
            data=self._registration_payload(
                username="forced_reporter_user",
                email="forced_reporter_user@example.com",
                role=UserProfile.Role.RESPONDER,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:login"))

        user = get_user_model().objects.get(username="forced_reporter_user")
        self.assertTrue(user.is_active)
        self.assertEqual(user.profile.role, UserProfile.Role.REPORTER)
        self.assertEqual(user.profile.verification_status, UserProfile.VerificationStatus.APPROVED)

    def test_responder_signup_stays_pending_for_admin_approval(self):
        response = self.client.post(
            reverse("disaster_management:register"),
            data=self._registration_payload(
                username="pending_responder",
                email="pending_responder@example.com",
                role=UserProfile.Role.RESPONDER,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:pending_approval"))

        user = get_user_model().objects.get(username="pending_responder")
        self.assertFalse(user.is_active)
        self.assertEqual(user.profile.role, UserProfile.Role.RESPONDER)
        self.assertEqual(user.profile.verification_status, UserProfile.VerificationStatus.PENDING)
        self.assertIsNone(user.profile.verified_at)

    def test_register_responder_route_ignores_tampered_role_payload(self):
        response = self.client.post(
            reverse("disaster_management:register_responder"),
            data=self._registration_payload(
                username="forced_responder_user",
                email="forced_responder_user@example.com",
                role=UserProfile.Role.REPORTER,
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("disaster_management:pending_approval"))

        user = get_user_model().objects.get(username="forced_responder_user")
        self.assertFalse(user.is_active)
        self.assertEqual(user.profile.role, UserProfile.Role.RESPONDER)
        self.assertEqual(user.profile.verification_status, UserProfile.VerificationStatus.PENDING)

    def test_verification_list_shows_only_pending_responder_signups(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username="signup_admin",
            email="signup_admin@example.com",
            password="pass12345",
        )
        reporter = user_model.objects.create_user(
            username="legacy_pending_reporter",
            email="legacy_pending_reporter@example.com",
            password="pass12345",
            is_active=False,
        )
        reporter.profile.role = UserProfile.Role.REPORTER
        reporter.profile.verification_status = UserProfile.VerificationStatus.PENDING
        reporter.profile.save(update_fields=["role", "verification_status", "updated_at"])

        responder = user_model.objects.create_user(
            username="pending_signup_responder",
            email="pending_signup_responder@example.com",
            password="pass12345",
            is_active=False,
        )
        responder.profile.role = UserProfile.Role.RESPONDER
        responder.profile.verification_status = UserProfile.VerificationStatus.PENDING
        responder.profile.save(update_fields=["role", "verification_status", "updated_at"])

        self.client.force_login(admin)
        response = self.client.get(reverse("disaster_management:verification_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, responder.username)
        self.assertNotContains(response, reporter.username)
