"""
URL configuration for rdms project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from diaster_management_app import views as app_views

urlpatterns = [
    path('dashboard/', app_views.dashboard_home, name='dashboard_home_fallback'),
    path('dashboard', app_views.dashboard_home, name='dashboard_home_fallback_noslash'),
    path('', include('diaster_management_app.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
