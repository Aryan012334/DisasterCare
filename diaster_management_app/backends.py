from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """
    Authenticate users with email + password.
    """

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        login_email = (email or username or "").strip()
        if not login_email or not password:
            return None

        UserModel = get_user_model()
        user = UserModel.objects.filter(email__iexact=login_email).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
