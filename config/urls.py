from django.contrib import admin
from django.urls import path, include

from apps.core.views_dashboard import DashboardDataView, DashboardView

urlpatterns = [
    path('admin/', admin.site.urls),
    # ── Dashboard visuel ────────────────────────────────────────
    path('walletx/dashboard/',       DashboardView.as_view(),     name='walletx-dashboard'),
    path('walletx/dashboard/data/',  DashboardDataView.as_view(), name='walletx-dashboard-data'),
    path('walletx/api/', include('apps.core.urls')),
]