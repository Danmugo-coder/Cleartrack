from django.urls import path
from . import views
from .views import request_demo_view
from .views import create_my_superuser

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('toggle-auto-sign/', views.toggle_auto_sign, name='toggle_auto_sign'),
    path('activate-subscription/<str:plan>/', views.activate_subscription, name='activate_subscription'),
    path('invoice/', views.generate_invoice, name='generate_invoice'),
    path('download-invoice/', views.download_invoice, name='download_invoice'),
    path('subscription/', views.subscription_view, name='subscription'),
    path('request-demo/', views.request_demo_view, name='request_demo'),
    path('create-my-admin/', views.create_my_superuser, name='create_my_superuser'),
]
