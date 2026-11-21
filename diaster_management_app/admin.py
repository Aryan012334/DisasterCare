"""
Django admin registration for DisasterCare.
Provides admin access to all major models while keeping the custom dashboard as primary UI.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    DamageAssessment,
    DisasterRiskZone,
    EarlyWarning,
    EvacuationPlan,
    InfrastructureAsset,
    IndianDistrict,
    IndianState,
    Incident,
    IncidentAssignment,
    IncidentFakeReport,
    IncidentUpdate,
    LocationSubscription,
    MitigationTask,
    NotificationLog,
    ReliefResource,
    ResourceCategory,
    ResourceRequest,
    ResponderProfile,
    Shelter,
    UserProfile,
)


# --- Add Django admin site header ---
admin.site.site_header = "DisasterCare Admin"
admin.site.site_title = "DisasterCare"
admin.site.index_title = "Administration"


@admin.register(IndianState)
class IndianStateAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_union_territory')
    search_fields = ('name', 'code')
    list_filter = ('is_union_territory',)


@admin.register(IndianDistrict)
class IndianDistrictAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'lgd_code')
    search_fields = ('name', 'lgd_code')
    list_filter = ('state',)
    autocomplete_fields = ('state',)


@admin.register(DisasterRiskZone)
class DisasterRiskZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'hazard_type', 'risk_level', 'risk_score', 'state', 'is_active')
    list_filter = ('hazard_type', 'risk_level', 'is_active', 'state')
    search_fields = ('name', 'description')


@admin.register(MitigationTask)
class MitigationTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'risk_zone', 'priority', 'status', 'due_date')
    list_filter = ('status', 'priority')
    search_fields = ('title',)


@admin.register(EarlyWarning)
class EarlyWarningAdmin(admin.ModelAdmin):
    list_display = ('title', 'disaster_type', 'severity', 'source', 'is_active', 'is_verified', 'issued_at')
    list_filter = ('disaster_type', 'severity', 'source', 'is_active', 'is_verified')
    search_fields = ('title', 'description')
    filter_horizontal = ('affected_states', 'affected_districts')
    actions = ['verify_warnings', 'deactivate_warnings']

    def verify_warnings(self, request, queryset):
        queryset.update(is_verified=True)
    verify_warnings.short_description = "Mark selected warnings as verified"

    def deactivate_warnings(self, request, queryset):
        queryset.update(is_active=False)
    deactivate_warnings.short_description = "Deactivate selected warnings"


@admin.register(Shelter)
class ShelterAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'status', 'capacity', 'current_occupancy')
    list_filter = ('status', 'state', 'has_food', 'has_water')
    search_fields = ('name', 'address')


@admin.register(EvacuationPlan)
class EvacuationPlanAdmin(admin.ModelAdmin):
    list_display = ('title', 'state', 'status', 'affected_population', 'created_at')
    list_filter = ('status', 'state')
    filter_horizontal = ('shelters',)


@admin.register(ReliefResource)
class ReliefResourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'quantity_available', 'quantity_reserved', 'unit', 'state')
    list_filter = ('category', 'state')
    search_fields = ('name',)


@admin.register(ResourceCategory)
class ResourceCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(ResourceRequest)
class ResourceRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'resource', 'requested_by', 'quantity_requested', 'quantity_approved', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(InfrastructureAsset)
class InfrastructureAssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'state', 'district', 'is_operational', 'is_sensitive')
    list_filter = ('category', 'is_operational', 'is_sensitive', 'state')
    search_fields = ('name', 'address')


@admin.register(DamageAssessment)
class DamageAssessmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'incident', 'severity', 'status', 'homes_damaged', 'estimated_loss_lakhs', 'assessed_by', 'created_at')
    list_filter = ('severity', 'status')
    search_fields = ('incident__title', 'description')


@admin.register(LocationSubscription)
class LocationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'state', 'district', 'notify_in_app', 'notify_email', 'created_at')
    list_filter = ('notify_in_app', 'notify_email', 'state')


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'incident_type', 'severity', 'status', 'reporter', 'created_at')
    list_filter = ('status', 'incident_type', 'severity', 'report_mode')
    search_fields = ('title', 'description', 'address')


@admin.register(IncidentUpdate)
class IncidentUpdateAdmin(admin.ModelAdmin):
    list_display = ('incident', 'status', 'actor', 'created_at')
    list_filter = ('status',)


@admin.register(IncidentAssignment)
class IncidentAssignmentAdmin(admin.ModelAdmin):
    list_display = ('incident', 'responder', 'status', 'distance_km', 'created_at')
    list_filter = ('status',)


@admin.register(IncidentFakeReport)
class IncidentFakeReportAdmin(admin.ModelAdmin):
    list_display = ('incident', 'responder', 'review_status', 'admin_action', 'created_at')
    list_filter = ('review_status', 'admin_action')


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'target_type', 'subject', 'channel', 'is_read', 'created_at')
    list_filter = ('target_type', 'channel', 'is_read')


@admin.register(ResponderProfile)
class ResponderProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'user', 'is_available', 'is_verified', 'response_points')
    list_filter = ('is_available', 'is_verified')
    search_fields = ('full_name',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'verification_status', 'is_blocked')
    list_filter = ('role', 'verification_status', 'is_blocked')
