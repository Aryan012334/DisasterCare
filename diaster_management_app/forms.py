from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm

from .models import (
    Incident,
    IncidentFakeReport,
    IncidentFinalReview,
    ResponderProfile,
    ResponderUpgradeRequest,
    UserProfile,
)


MAX_PROFILE_PHOTO_SIZE_BYTES = 3 * 1024 * 1024
MAX_ATTACHMENT_SIZE_BYTES = 15 * 1024 * 1024
PASSWORD_RESET_OTP_LENGTH = max(4, int(getattr(settings, "PASSWORD_RESET_OTP_LENGTH", 6)))

PROFILE_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PROFILE_PHOTO_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

INCIDENT_ATTACHMENT_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".pdf",
    ".doc",
    ".docx",
}
INCIDENT_ATTACHMENT_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _clean_name_without_digits(value, *, label):
    cleaned = (value or "").strip()
    if any(char.isdigit() for char in cleaned):
        raise forms.ValidationError(f"{label} cannot contain numbers.")
    return cleaned


def _validate_uploaded_file(file_obj, *, allowed_extensions, allowed_content_types, max_size_bytes, label):
    if not file_obj:
        return

    suffix = Path(file_obj.name or "").suffix.lower()
    if not suffix or suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise forms.ValidationError(f"This {label} type is not supported. Allowed file types: {allowed}.")

    if file_obj.size > max_size_bytes:
        max_mb = int(max_size_bytes / (1024 * 1024))
        raise forms.ValidationError(f"{label.title()} is too large. Maximum allowed size is {max_mb} MB.")

    content_type = (getattr(file_obj, "content_type", "") or "").lower()
    if content_type and content_type not in allowed_content_types:
        raise forms.ValidationError(f"This {label} format is not supported.")


def _validate_incident_attachments(files):
    for file_obj in files or []:
        _validate_uploaded_file(
            file_obj,
            allowed_extensions=INCIDENT_ATTACHMENT_EXTENSIONS,
            allowed_content_types=INCIDENT_ATTACHMENT_CONTENT_TYPES,
            max_size_bytes=MAX_ATTACHMENT_SIZE_BYTES,
            label="attachment",
        )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class UserRegistrationForm(UserCreationForm):
    first_name = forms.CharField(required=True, max_length=150)
    last_name = forms.CharField(required=True, max_length=150)
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(choices=UserProfile.Role.choices, required=True)
    profile_photo = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("first_name", "last_name", "username", "email", "password1", "password2", "role")

    def clean_first_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("first_name"), label="First name")
        if not value:
            raise forms.ValidationError("First name is required.")
        return value

    def clean_last_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("last_name"), label="Last name")
        if not value:
            raise forms.ValidationError("Last name is required.")
        return value

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        user_model = get_user_model()
        if user_model.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email

    def clean_profile_photo(self):
        file_obj = self.cleaned_data.get("profile_photo")
        _validate_uploaded_file(
            file_obj,
            allowed_extensions=PROFILE_PHOTO_EXTENSIONS,
            allowed_content_types=PROFILE_PHOTO_CONTENT_TYPES,
            max_size_bytes=MAX_PROFILE_PHOTO_SIZE_BYTES,
            label="profile photo",
        )
        return file_obj


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("first_name", "last_name", "email")
        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "First name",
                    "pattern": "[^0-9]*",
                    "title": "First name cannot contain numbers.",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Last name",
                    "pattern": "[^0-9]*",
                    "title": "Last name cannot contain numbers.",
                }
            ),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email"}),
        }

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop("user_instance", None)
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        user_model = get_user_model()
        qs = user_model.objects.filter(email__iexact=email)
        if self.user_instance is not None:
            qs = qs.exclude(id=self.user_instance.id)
        if qs.exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_first_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("first_name"), label="First name")
        if not value:
            raise forms.ValidationError("First name is required.")
        return value

    def clean_last_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("last_name"), label="Last name")
        if not value:
            raise forms.ValidationError("Last name is required.")
        return value


class UserProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("profile_photo",)
        widgets = {
            "profile_photo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("is_superuser", None)
        super().__init__(*args, **kwargs)

    def clean_profile_photo(self):
        file_obj = self.cleaned_data.get("profile_photo")
        _validate_uploaded_file(
            file_obj,
            allowed_extensions=PROFILE_PHOTO_EXTENSIONS,
            allowed_content_types=PROFILE_PHOTO_CONTENT_TYPES,
            max_size_bytes=MAX_PROFILE_PHOTO_SIZE_BYTES,
            label="profile photo",
        )
        return file_obj


class PasswordResetOTPRequestForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "name@example.com",
            }
        )
    )

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()


class PasswordResetOTPVerifyForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "name@example.com",
            }
        )
    )
    otp = forms.CharField(
        min_length=PASSWORD_RESET_OTP_LENGTH,
        max_length=PASSWORD_RESET_OTP_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": f"{PASSWORD_RESET_OTP_LENGTH}-digit code",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "pattern": f"[0-9]{{{PASSWORD_RESET_OTP_LENGTH}}}",
            }
        ),
    )

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_otp(self):
        otp = (self.cleaned_data.get("otp") or "").strip()
        if not otp.isdigit():
            raise forms.ValidationError("Enter numbers only.")
        return otp


class PasswordResetSetPasswordForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "New password",
            }
        )
        self.fields["new_password2"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "Confirm new password",
            }
        )


class IncidentReportForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*,video/*,.pdf,.doc,.docx",
            }
        ),
    )

    class Meta:
        model = Incident
        fields = (
            "title",
            "incident_type",
            "severity",
            "description",
            "address",
            "occurred_at",
            "people_affected",
            "contact_phone",
            "affected_radius_km",
            "latitude",
            "longitude",
        )
        labels = {
            "title": "Short title",
            "incident_type": "Type",
            "severity": "How serious is it?",
            "description": "What happened?",
            "address": "Place / nearby landmark",
            "occurred_at": "When it happened",
            "people_affected": "How many people are affected?",
            "contact_phone": "Phone number (optional)",
            "affected_radius_km": "Affected area (km)",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Short title"}),
            "incident_type": forms.Select(attrs={"class": "form-select"}),
            "severity": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Explain what happened"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nearby landmark or address"}),
            "occurred_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "people_affected": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Your contact number (optional)"}),
            "affected_radius_km": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "min": "0.1"}),
            "latitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
            "longitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
        }

    def clean_latitude(self):
        value = self.cleaned_data["latitude"]
        if value < -90 or value > 90:
            raise forms.ValidationError("Latitude should be between -90 and 90.")
        return value

    def clean_longitude(self):
        value = self.cleaned_data["longitude"]
        if value < -180 or value > 180:
            raise forms.ValidationError("Longitude should be between -180 and 180.")
        return value

    def clean_people_affected(self):
        value = self.cleaned_data["people_affected"]
        if value > 100000:
            raise forms.ValidationError("The number of affected people looks too high.")
        return value

    def clean_affected_radius_km(self):
        value = self.cleaned_data["affected_radius_km"]
        if value <= 0 or value > 50:
            raise forms.ValidationError("Affected area should be between 0.1 and 50 km.")
        return value

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments", [])
        _validate_incident_attachments(files)
        return files

class EmergencyIncidentReportForm(forms.ModelForm):
    confirm_emergency_policy = forms.BooleanField(
        required=True,
        label="I understand this will send an immediate emergency alert, and fake alerts can lead to account suspension.",
    )
    attachments = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*,video/*,.pdf,.doc,.docx",
            }
        ),
    )

    class Meta:
        model = Incident
        fields = (
            "title",
            "incident_type",
            "severity",
            "description",
            "address",
            "people_affected",
            "contact_phone",
            "affected_radius_km",
            "latitude",
            "longitude",
        )
        labels = {
            "title": "Emergency title",
            "incident_type": "Type",
            "severity": "How serious is it?",
            "description": "What is happening right now?",
            "address": "Place / nearby landmark",
            "people_affected": "How many people are affected? (optional)",
            "contact_phone": "Phone number (optional)",
            "affected_radius_km": "Affected area (km)",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Emergency title"}),
            "incident_type": forms.Select(attrs={"class": "form-select"}),
            "severity": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Brief emergency details"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nearby landmark or address"}),
            "people_affected": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Your contact number (optional)"}),
            "affected_radius_km": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "min": "0.1"}),
            "latitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
            "longitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["severity"].choices = [
            (Incident.Severity.HIGH, "High"),
            (Incident.Severity.CRITICAL, "Critical"),
        ]
        self.fields["people_affected"].required = False
        self.fields["affected_radius_km"].required = False
        if not self.initial.get("severity"):
            self.initial["severity"] = Incident.Severity.CRITICAL

    def clean_latitude(self):
        value = self.cleaned_data["latitude"]
        if value < -90 or value > 90:
            raise forms.ValidationError("Latitude should be between -90 and 90.")
        return value

    def clean_longitude(self):
        value = self.cleaned_data["longitude"]
        if value < -180 or value > 180:
            raise forms.ValidationError("Longitude should be between -180 and 180.")
        return value

    def clean_people_affected(self):
        value = self.cleaned_data.get("people_affected")
        if value in (None, ""):
            return 0
        if value > 100000:
            raise forms.ValidationError("The number of affected people looks too high.")
        return value

    def clean_affected_radius_km(self):
        value = self.cleaned_data.get("affected_radius_km")
        if value in (None, ""):
            return 3.0
        if value <= 0 or value > 50:
            raise forms.ValidationError("Affected area should be between 0.1 and 50 km.")
        return value

    def clean_severity(self):
        severity = self.cleaned_data["severity"]
        if severity not in {Incident.Severity.HIGH, Incident.Severity.CRITICAL}:
            raise forms.ValidationError("For emergency reports, please select High or Critical.")
        return severity

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments", [])
        _validate_incident_attachments(files)
        return files


class IncidentStatusUpdateForm(forms.Form):
    status = forms.ChoiceField(choices=Incident.Status.choices, widget=forms.Select(attrs={"class": "form-select"}))
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional note"}),
    )


class IncidentStatusEnquiryRequestForm(forms.Form):
    request_note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Optional: tell the response team what update you need.",
            }
        ),
    )


class IncidentStatusEnquiryResponseForm(forms.Form):
    current_status = forms.ChoiceField(widget=forms.Select(attrs={"class": "form-select"}))
    responder_note = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Share your latest on-site update and actions completed.",
            }
        ),
    )
    recommend_finalize = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Recommend closing this case after review",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["current_status"].choices = [
            (Incident.Status.DISPATCHED, "Dispatched"),
            (Incident.Status.ON_SCENE, "On Scene"),
            (Incident.Status.STABILIZED, "Stabilized"),
            (Incident.Status.ESCALATED, "Escalated"),
        ]

    def clean_responder_note(self):
        value = (self.cleaned_data.get("responder_note") or "").strip()
        if len(value) < 8:
            raise forms.ValidationError("Please enter at least 8 characters.")
        return value


class IncidentStatusEnquiryFinalizeForm(forms.Form):
    admin_note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Optional closing note.",
            }
        ),
    )
    close_incident = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Close this case now if all checks are complete",
    )


class IncidentStatusEnquiryOverdueActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=[
            ("warning", "Send warning to team member"),
            ("suspend", "Temporarily suspend team member account"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    admin_note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Optional note.",
            }
        ),
    )


class IncidentFinalReviewForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*,video/*,.pdf,.doc,.docx",
            }
        ),
    )

    class Meta:
        model = IncidentFinalReview
        fields = ("summary", "verification_notes", "recommend_close")
        labels = {
            "summary": "Final summary",
            "verification_notes": "Extra notes",
            "recommend_close": "Recommend closing this case",
        }
        widgets = {
            "summary": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Final update summary"}
            ),
            "verification_notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "What was checked on site"}
            ),
            "recommend_close": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_summary(self):
        value = self.cleaned_data["summary"].strip()
        if len(value) < 12:
            raise forms.ValidationError("Please provide a little more detail.")
        return value

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments", [])
        _validate_incident_attachments(files)
        return files


class IncidentFakeReportForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "accept": "image/*,video/*,.pdf,.doc,.docx",
            }
        ),
    )

    class Meta:
        model = IncidentFakeReport
        fields = ("reason",)
        widgets = {
            "reason": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Why this report may be fake"}
            ),
        }

    def clean_reason(self):
        value = self.cleaned_data["reason"].strip()
        if len(value) < 10:
            raise forms.ValidationError("Please share a bit more detail for review.")
        return value

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments", [])
        _validate_incident_attachments(files)
        return files


class ResponderUpgradeRequestForm(forms.ModelForm):
    class Meta:
        model = ResponderUpgradeRequest
        fields = ("full_name", "phone_number", "skills", "reason")
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Full name",
                    "pattern": "[^0-9]*",
                    "title": "Full name cannot contain numbers.",
                }
            ),
            "phone_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Contact phone"}),
            "skills": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Emergency/field skills"}),
            "reason": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 2,
                    "placeholder": "Why do you want to join the response team?",
                }
            ),
        }

    def clean_full_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("full_name"), label="Full name")
        if len(value) < 3:
            raise forms.ValidationError("Full name must be at least 3 characters.")
        return value

    def clean_phone_number(self):
        value = (self.cleaned_data.get("phone_number") or "").strip()
        if len(value) < 8:
            raise forms.ValidationError("Phone number looks too short.")
        return value

    def clean_reason(self):
        value = (self.cleaned_data.get("reason") or "").strip()
        if value and len(value) < 10:
            raise forms.ValidationError("If you add a reason, please enter at least 10 characters.")
        return value


class ResponderProfileForm(forms.ModelForm):
    class Meta:
        model = ResponderProfile
        fields = ("full_name", "phone_number", "skills", "is_available", "latitude", "longitude")
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Full name",
                    "pattern": "[^0-9]*",
                    "title": "Full name cannot contain numbers.",
                }
            ),
            "phone_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone"}),
            "skills": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Skills"}),
            "is_available": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "latitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
            "longitude": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["latitude"].required = True
        self.fields["longitude"].required = True

    def clean_full_name(self):
        value = _clean_name_without_digits(self.cleaned_data.get("full_name"), label="Full name")
        if len(value) < 3:
            raise forms.ValidationError("Full name must be at least 3 characters.")
        return value

    def clean(self):
        cleaned = super().clean()
        lat = cleaned.get("latitude")
        lon = cleaned.get("longitude")
        if lat is None or lon is None:
            raise forms.ValidationError("Location is required. Please enter latitude and longitude.")
        if lat < -90 or lat > 90:
            raise forms.ValidationError("Latitude should be between -90 and 90.")
        if lon < -180 or lon > 180:
            raise forms.ValidationError("Longitude should be between -180 and 180.")
        return cleaned
