from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),  # Home page
    path('scan-clearance/', views.scan_clearance, name='scan_clearance'),  # QR or token scan page
    path('view-clearance/<uuid:token>/', views.view_clearance, name='view_clearance'),  # View specific clearance
]
