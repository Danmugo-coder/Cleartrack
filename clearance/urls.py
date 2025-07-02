from django.urls import path
from . import views

app_name = 'clearance'
 
urlpatterns = [
    path('<uuid:token>/', views.clearance_detail, name='clearance_detail'),
    path('sign/<int:field_id>/', views.sign_clearance, name='sign_clearance'),
] 
