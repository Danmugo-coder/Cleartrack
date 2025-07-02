from django.urls import path
from . import views
from .views import subscription_view

app_name = 'dashboard'

urlpatterns = [
    # Admin URLs
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/users/', views.admin_users, name='admin_users'),
    path('admin/departments/', views.admin_departments, name='admin_departments'),
    path('admin/courses/', views.admin_courses, name='admin_courses'),
    path('admin/sections/', views.admin_sections, name='admin_sections'),
    path('admin/students/', views.admin_students, name='admin_students'),
    path('admin/clearance/', views.admin_clearance, name='admin_clearance'),
    path('admin/reports/', views.admin_reports, name='admin_reports'),
    path('admin/scan-qr/', views.admin_scan_qr, name='admin_scan_qr'),
    path('admin/scan-to-sign/', views.admin_scan_to_sign, name='admin_scan_to_sign'),
    path('admin/sign-approval/', views.sign_approval, name='sign_approval'),
    path('admin/templates/', views.admin_templates, name='admin_templates'),
    path('admin/landlords/', views.admin_landlords, name='admin_landlords'),

    # Import routes
    path('admin/import-students/', views.import_students, name='import_students'),
    path('admin/import-courses/', views.import_courses, name='import_courses'),

    # Export and Print routes
    path('admin/export-data/<str:data_type>/', views.export_data, name='export_data'),
    path('admin/export-pdf/', views.export_pdf, name='export_pdf'),
    path('admin/print-qr/<int:student_id>/', views.print_qr, name='print_qr'),
    path('admin/print-qr-batch/', views.print_qr_batch, name='print_qr_batch'),

    # Staff URLs
    path('staff/', views.staff_dashboard, name='staff_dashboard'),
    path('staff/profile/', views.staff_profile, name='staff_profile'),
    path('staff/signature/', views.staff_signature, name='staff_signature'),
    path('staff/scan/', views.staff_scan, name='staff_scan'),
    path('staff/reports/', views.staff_reports, name='staff_reports'),
    path('staff/history/', views.staff_history, name='staff_history'),

    # User URLs
    path('user/', views.user_dashboard, name='user_dashboard'),
    path('user/signature/', views.user_signature, name='user_signature'),
    path('user/scan/', views.user_scan, name='user_scan'),
    path('user/landlord-signatures/', views.landlord_signatures, name='landlord_signatures'),

    # API endpoints
    path('api/get-student/', views.get_student, name='get_student'),
    path('api/verify-qr/', views.verify_qr, name='verify_qr'),
    path('api/get-report-details/', views.get_report_details, name='get_report_details'),

    # Subscription
    path('subscription/', subscription_view, name='subscription'),  # ✅ Uses the correct imported view
    path('subscription/process/', views.process_payment, name='process_payment'),
    path('subscription/select-method/', views.select_payment_method, name='select_payment_method'),
    path('create-payment/', views.create_payment, name='create_payment'),
    path('payment-success/', views.payment_success, name='payment_success'),
    path('payment-cancelled/', views.payment_cancelled, name='payment_cancelled'),
]
