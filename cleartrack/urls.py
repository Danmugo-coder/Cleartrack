from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from accounts.views import create_my_superuser  # ✅ Import the view
from accounts.views import request_access  # ✅ Import request access view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("create-superuser/", create_my_superuser),  # ✅ Add this line
    path("", include(("core.urls", "core"), namespace="core")),
    path("accounts/", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("clearance/", include("clearance.urls")),
    path("request-access/", request_access, name="request_access"),  # ✅ New line
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
