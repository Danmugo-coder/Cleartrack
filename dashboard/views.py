from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, FileResponse
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum, F, Value
from accounts.models import UserProfile
from core.models import Course, YearSection, Department, SystemBackup
from clearance.models import Student, ClearanceTemplate, ClearanceForm, ClearanceField, DailyReport, ApprovalSignature, LandlordSignature
from django.contrib.sessions.models import Session
import json, random, os, uuid, base64, csv, re, io, xlsxwriter, tempfile
from datetime import datetime, timedelta
from django.core.files.base import ContentFile
from django.db import transaction, connection
from django.db.models import Count
from PIL import Image
from io import BytesIO
from django.urls import reverse
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import paypalrestsdk

# Configure PayPal once
paypalrestsdk.configure({
    "mode": "sandbox",  # Change to "live" in production
    "client_id": "AbTgDTWEV3u0ijhMqeJ3zrgjRmol3b-aiPLxH-H1XKnIQ1B4WB_8nQFbSWTXVMgd8OUE4qgBknEjxz0T",
    "client_secret": "EFkEGB_hogOkUgdn3ggtQQMCh7pEU3xCx_Fi7rb7JRkLhBlwJjNY1RGJp5ZX1chfJBLgyv9xot8ucnel"
})

@login_required
@csrf_exempt
def select_payment_method(request):
    """
    Show a page where users can choose a payment method (e.g., PayPal, M-Pesa, Stripe).
    """
    if request.method == "POST":
        plan = request.POST.get("plan")
        billing_cycle = request.POST.get("billing_cycle")  # <== get it from POST

        valid_plans = ["basic", "pro", "enterprise"]
        valid_cycles = ["monthly", "yearly"]

        if plan not in valid_plans or billing_cycle not in valid_cycles:
            messages.error(request, "Invalid plan or billing cycle.")
            return redirect('dashboard:subscription')

        # Store in session for use in the next step
        request.session['plan'] = plan
        request.session['billing_cycle'] = billing_cycle

        return render(request, 'dashboard/payment_method_selection.html', {
            "plan": plan,
            "billing_cycle": billing_cycle,
        })

    return redirect('dashboard:subscription')

@login_required
def create_payment(request):
    """
    Creates a PayPal payment based on the selected plan
    """
    plan = request.GET.get("plan", "pro")  # Default to 'pro' if none
    plan_prices = {
        "basic": "15.00",
        "pro": "25.00",
        "enterprise": "30.00",
    }

    if plan not in plan_prices:
        return JsonResponse({"error": "Invalid plan selected"}, status=400)

    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {"payment_method": "paypal"},
        "redirect_urls": {
            "return_url": request.build_absolute_uri("/dashboard/payment-success/"),
            "cancel_url": request.build_absolute_uri("/dashboard/payment-cancelled/")
        },
        "transactions": [{
            "item_list": {
                "items": [{
                    "name": f"{plan.capitalize()} Plan Subscription",
                    "sku": plan,
                    "price": plan_prices[plan],
                    "currency": "USD",
                    "quantity": 1
                }]
            },
            "amount": {
                "total": plan_prices[plan],
                "currency": "USD"
            },
            "description": f"Monthly subscription for {plan.capitalize()} Plan"
        }]
    })

    if payment.create():
        for link in payment.links:
            if link.rel == "approval_url":
                return HttpResponseRedirect(link.href)
        return JsonResponse({"error": "No redirect URL found."})
    else:
        return JsonResponse({
            "error": "Payment creation failed",
            "details": payment.error
        }, status=500)

# Create your views here.
def get_all_landlord_users():
    """
    Get all users with is_landlord=True in their profile
    """
    return User.objects.filter(profile__is_landlord=True)

def get_daily_clearance_history(days=7):
    """
    Get daily clearance completion data for the past specified days
    Returns a dictionary with dates, completed counts, and pending counts
    """
    today = timezone.localtime().date()
    
    # For longer time periods, we'll aggregate by week or month to avoid too many data points
    if days > 90:  # For a year, aggregate by month
        result = {
            'dates': [],
            'completed': [],
            'pending': []
        }
        
        # Get data for the last 12 months
        for i in range(11, -1, -1):
            # Calculate month start and end
            month_date = today.replace(day=1) - timedelta(days=i*30)  # Approximate
            month_name = month_date.strftime('%b %Y')
            
            # Get completed and total counts for this month
            month_start = month_date.replace(day=1)
            if i > 0:
                next_month = month_date.replace(day=28) + timedelta(days=4)
                month_end = next_month.replace(day=1) - timedelta(days=1)
            else:
                month_end = today
            
            completed_in_month = ClearanceField.objects.filter(
                signed_at__date__gte=month_start,
                signed_at__date__lte=month_end,
                status=True
            ).count()
            
            total_in_month = ClearanceField.objects.filter(
                form__created_at__date__lte=month_end
            ).count()
            
            pending_in_month = total_in_month - completed_in_month if total_in_month > completed_in_month else 0
            
            result['dates'].append(month_name)
            result['completed'].append(completed_in_month)
            result['pending'].append(pending_in_month)
    
    elif days > 30:  # For 90 days, aggregate by week
        result = {
            'dates': [],
            'completed': [],
            'pending': []
        }
        
        # Get data for the last 12 weeks
        for i in range(11, -1, -1):
            week_end = today - timedelta(days=i*7)
            week_start = week_end - timedelta(days=6)
            week_label = f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}"
            
            completed_in_week = ClearanceField.objects.filter(
                signed_at__date__gte=week_start,
                signed_at__date__lte=week_end,
                status=True
            ).count()
            
            total_in_week = ClearanceField.objects.filter(
                form__created_at__date__lte=week_end
            ).count()
            
            pending_in_week = total_in_week - completed_in_week if total_in_week > completed_in_week else 0
            
            result['dates'].append(week_label)
            result['completed'].append(completed_in_week)
            result['pending'].append(pending_in_week)
    
    else:  # For 7 or 30 days, show daily data
        # For longer periods like 30 days, we might want to sample every few days
        step = 1 if days <= 14 else (2 if days <= 30 else 3)
        date_range = [today - timedelta(days=i) for i in range(days-1, -1, -step)]
        
        result = {
            'dates': [],
            'completed': [],
            'pending': []
        }
        
        for date in date_range:
            # Format date for display
            result['dates'].append(date.strftime('%b %d'))
            
            # Get clearance forms that were completed on this date
            completed_on_date = ClearanceField.objects.filter(
                signed_at__date=date,
                status=True
            ).count()
            
            # Get total clearance forms on this date
            total_on_date = ClearanceField.objects.filter(
                form__created_at__date__lte=date
            ).count()
            
            # Calculate pending
            pending_on_date = total_on_date - completed_on_date if total_on_date > completed_on_date else 0
            
            result['completed'].append(completed_on_date)
            result['pending'].append(pending_on_date)
    
    return result

# Admin dashboard views
@login_required
def admin_dashboard(request):
    """
    Admin dashboard view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Get time range parameter from request
    time_range = request.GET.get('time_range', '7d')
    
    # Map time range to number of days
    days_mapping = {
        '7d': 7,      # Last 7 days
        '30d': 30,    # Last 30 days
        '90d': 90,    # Last 90 days (about 3 months)
        '1y': 365,    # Last year
    }
    
    # Default to 7 days if invalid time range
    days = days_mapping.get(time_range, 7)
    
    # Get basic counts for dashboard cards
    students_count = Student.objects.count()
    complete_clearance = ClearanceForm.objects.filter(status='complete').count()
    incomplete_clearance = ClearanceForm.objects.filter(status='incomplete').count()
    
    # Get recent clearance activity
    recent_clearances = ClearanceField.objects.filter(
        status=True
    ).order_by('-signed_at')[:10]
    
    # Calculate clearance completion rate
    clearance_rate = 0
    if complete_clearance + incomplete_clearance > 0:
        clearance_rate = (complete_clearance / (complete_clearance + incomplete_clearance)) * 100
    
    # Get staff with most clearance signatures
    staff_signatures = User.objects.filter(
        assigned_fields__status=True
    ).annotate(
        signatures_count=Count('assigned_fields')
    ).order_by('-signatures_count')[:5]
    
    # Get daily report submissions
    today = timezone.localtime().date()
    one_week_ago = today - timedelta(days=7)
    
    daily_reports = DailyReport.objects.filter(
        date__gte=one_week_ago
    ).values('date').annotate(
        total=Count('id'),
        submitted=Count('id', filter=Q(submitted=True))
    ).order_by('date')
    
    # Get department range parameter from request
    dept_range = request.GET.get('dept_range', 'all')
    
    # Base query for departments
    dept_query = Department.objects.all()
    
    # Build filter conditions for students based on department range
    student_filter = Q()
    current_year = timezone.now().year
    
    if dept_range == 'year':
        # Filter students created in the current year
        student_filter = Q(students__created_at__year=current_year)
    elif dept_range == 'semester':
        # Determine current semester (simplified - assuming Jan-Jun is 1st semester, Jul-Dec is 2nd)
        current_month = timezone.now().month
        semester_start = timezone.datetime(current_year, 1 if current_month < 7 else 7, 1)
        student_filter = Q(students__created_at__gte=semester_start)
    
    # Get departments with statistics
    departments = dept_query.annotate(
        students_count=Count('students', filter=student_filter) if dept_range != 'all' else Count('students'),
        courses_count=Count('courses'),
        completed_count=Count('students', filter=student_filter & Q(students__clearance_status='complete')) 
                      if dept_range != 'all' else Count('students', filter=Q(students__clearance_status='complete')),
        incomplete_count=Count('students', filter=student_filter & Q(students__clearance_status='in_progress'))
                      if dept_range != 'all' else Count('students', filter=Q(students__clearance_status='in_progress'))
    ).order_by('-students_count')
    
    # Calculate department completion rates
    for department in departments:
        if department.students_count > 0:
            department.completion_rate = (department.completed_count / department.students_count) * 100
        else:
            department.completion_rate = 0
    
    # Get courses with statistics for course analysis chart
    courses = Course.objects.annotate(
        students_count=Count('students'),
        completed_count=Count('students', filter=Q(students__clearance_status='complete')),
        incomplete_count=Count('students', filter=Q(students__clearance_status='in_progress'))
    ).order_by('-students_count')
    
    # Calculate course completion rates
    for course in courses:
        if course.students_count > 0:
            course.completion_rate = (course.completed_count / course.students_count) * 100
        else:
            course.completion_rate = 0
    
    # Get historical clearance data based on selected time range
    clearance_history = get_daily_clearance_history(days)
    
    # Generate weekly completion rate data for trend analysis
    # This simulates historical data - in a real app, you would query actual historical data
    current_rate = round(clearance_rate, 1)
    
    
    # Start with a lower rate and gradually increase to current rate
    base_rate = max(0, current_rate - random.uniform(15, 25))
    weekly_rates = []
    
    for i in range(4):  # 4 previous weeks
        # Gradually increase with some randomness
        week_rate = base_rate + (current_rate - base_rate) * (i / 4) + random.uniform(-5, 5)
        week_rate = max(0, min(100, week_rate))  # Keep between 0-100%
        weekly_rates.append(round(week_rate, 1))
    
    # Add current rate
    weekly_rates.append(current_rate)
    
    # Convert to JSON for template
    weekly_completion_rates = json.dumps(weekly_rates)
    
    # System status
    system_status = "Operational"
    
    # Current academic term - determine based on current date
    current_year = timezone.now().year
    current_month = timezone.now().month
    
    # Determine semester based on month
    if 1 <= current_month <= 5:  # January to May
        current_semester = "2nd Semester"
    elif 6 <= current_month <= 7:  # June to July
        current_semester = "Summer"
    else:  # August to December
        current_semester = "1st Semester"
        
    current_term = f"{current_year} - {current_semester}"
    
    # Database size - get from connection
    with connection.cursor() as cursor:
        # For SQLite, get the file size
        db_path = connection.settings_dict['NAME']
        try:
            db_size_bytes = os.path.getsize(db_path)
            db_size = f"{db_size_bytes / (1024 * 1024):.2f} MB"
        except:
            db_size = "Unknown"

    
    # Get active sessions
    active_sessions = Session.objects.filter(
        expire_date__gte=timezone.now()
    ).count()
    
    # Use active sessions as proxy for active users
    active_users_today = active_sessions
    
    # Last backup - get from SystemBackup model if available
    try:
        latest_backup = SystemBackup.objects.filter(is_successful=True).latest('created_at')
        last_backup = latest_backup.created_at.strftime("%b %d, %I:%M %p")
    except (SystemBackup.DoesNotExist, Exception):
        # Fallback to DB file modification time
        try:
            last_backup_time = datetime.fromtimestamp(os.path.getmtime(db_path))
            last_backup = last_backup_time.strftime("%b %d, %I:%M %p")
        except:
            last_backup = "Not available"
    
    context = {
        'students_count': students_count,
        'complete_clearance': complete_clearance,
        'incomplete_clearance': incomplete_clearance,
        'clearance_rate': round(clearance_rate, 1),
        'recent_clearances': recent_clearances,
        'staff_signatures': staff_signatures,
        'daily_reports': daily_reports,
        'departments': departments,
        'courses': courses,
        'weekly_completion_rates': weekly_completion_rates,
        'clearance_history': clearance_history,  # Historical clearance data
        'time_range': time_range,  # Current time range selection
        'dept_range': dept_range,  # Current department range selection
        
        # System information
        'system_status': system_status,
        'current_term': current_term,
        'db_size': db_size,
        'active_users_today': active_users_today,
        'last_backup': last_backup,
    }
    
    return render(request, 'dashboard/admin/dashboard.html', context)

@login_required
def admin_users(request):
    """
    Admin users management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Handle form submissions
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'create':
            # Create new user
            username = request.POST.get('username')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name', '')
            last_name = request.POST.get('last_name', '')
            password = request.POST.get('password')
            user_type = request.POST.get('user_type', 'staff')
            department_id = request.POST.get('department_id')
            is_landlord = request.POST.get('is_landlord') == 'true'
            
            if username and email and password:
                try:
                    # Check if username exists
                    if User.objects.filter(username=username).exists():
                        messages.error(request, f'Username "{username}" is already taken.')
                    # Check if email exists
                    elif User.objects.filter(email=email).exists():
                        messages.error(request, f'Email "{email}" is already registered.')
                    else:
                        # Create user
                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            password=password,
                            first_name=first_name,
                            last_name=last_name
                        )
                        
                        # Update user profile (already created by signal)
                        from accounts.models import UserProfile
                        user.profile.user_type = user_type
                        user.profile.is_landlord = is_landlord
                        
                        # Set department if provided and not a landlord
                        if department_id and not is_landlord:
                            try:
                                department = Department.objects.get(id=department_id)
                                user.profile.department = department
                            except Department.DoesNotExist:
                                pass
                                
                        user.profile.save()
                        
                        messages.success(request, f'User "{username}" created successfully.')
                except Exception as e:
                    messages.error(request, f'Error creating user: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'update':
            # Update existing user
            user_id = request.POST.get('user_id')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name', '')
            last_name = request.POST.get('last_name', '')
            user_type = request.POST.get('user_type')
            department_id = request.POST.get('department_id')
            is_landlord = request.POST.get('is_landlord') == 'true'
            new_password = request.POST.get('new_password', '')
            
            if user_id and email:
                try:
                    # Get user
                    user = User.objects.get(id=user_id)
                    
                    # Check if email exists for another user
                    if User.objects.filter(email=email).exclude(id=user_id).exists():
                        messages.error(request, f'Email "{email}" is already registered to another user.')
                    else:
                        # Update user
                        user.email = email
                        user.first_name = first_name
                        user.last_name = last_name
                        
                        # Update password if provided
                        if new_password:
                            user.set_password(new_password)
                        
                        user.save()
                        
                        # Update user profile
                        if hasattr(user, 'profile'):
                            user.profile.user_type = user_type
                            user.profile.is_landlord = is_landlord
                            
                            # Update department if not a landlord
                            if not is_landlord and department_id:
                                try:
                                    department = Department.objects.get(id=department_id)
                                    user.profile.department = department
                                except Department.DoesNotExist:
                                    pass
                            elif is_landlord:
                                # Clear department if user is a landlord
                                user.profile.department = None
                                
                            user.profile.save()
                        
                        messages.success(request, f'User "{user.username}" updated successfully.')
                except User.DoesNotExist:
                    messages.error(request, 'User not found.')
                except Exception as e:
                    messages.error(request, f'Error updating user: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'delete':
            # Delete user
            user_id = request.POST.get('user_id')
            
            if user_id:
                try:
                    # Prevent deleting self
                    if int(user_id) == request.user.id:
                        messages.error(request, 'You cannot delete your own account.')
                    else:
                        user = User.objects.get(id=user_id)
                        username = user.username
                        
                        # Check if user has assigned clearance fields
                        from clearance.models import ClearanceField
                        if ClearanceField.objects.filter(assigned_to=user).exists():
                            messages.error(request, f'Cannot delete user "{username}" because they have assigned clearance fields.')
                        else:
                            # Delete user
                            user.delete()
                            messages.success(request, f'User "{username}" deleted successfully.')
                except User.DoesNotExist:
                    messages.error(request, 'User not found.')
                except Exception as e:
                    messages.error(request, f'Error deleting user: {str(e)}')
            else:
                messages.error(request, 'Invalid user ID.')
        
        return redirect('dashboard:admin_users')
    
    # Get all users with their profiles
    users = User.objects.all().order_by('username')
    
    # Get all departments for dropdown
    departments = Department.objects.all().order_by('name')
    
    context = {
        'users': users,
        'departments': departments,
    }
    
    return render(request, 'dashboard/admin/users.html', context)

@login_required
def admin_courses(request):
    """
    Admin courses management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'add':
            # Add new course
            name = request.POST.get('name', '').strip()
            department_id = request.POST.get('department_id')
            
            if name:
                # Check if course with same name exists
                if Course.objects.filter(name=name).exists():
                    messages.error(request, f'Course "{name}" already exists')
                else:
                    # Get department if provided
                    department = None
                    if department_id:
                        try:
                            department = Department.objects.get(id=department_id)
                        except Department.DoesNotExist:
                            pass
                    
                    course = Course.objects.create(name=name, department=department)
                    messages.success(request, f'Course "{name}" added successfully')
            else:
                messages.error(request, 'Please enter a course name')
        
        elif action == 'edit':
            # Edit course
            course_id = request.POST.get('course_id')
            name = request.POST.get('name', '').strip()
            department_id = request.POST.get('department_id')
            
            if course_id and name:
                try:
                    course = Course.objects.get(id=course_id)
                    
                    # Check if another course with same name exists
                    if Course.objects.filter(name=name).exclude(id=course_id).exists():
                        messages.error(request, f'Course "{name}" already exists')
                    else:
                        course.name = name
                        
                        # Update department if provided
                        if department_id:
                            try:
                                department = Department.objects.get(id=department_id)
                                course.department = department
                            except Department.DoesNotExist:
                                pass
                        else:
                            course.department = None
                        
                        course.save()
                        messages.success(request, f'Course "{name}" updated successfully')
                except Course.DoesNotExist:
                    messages.error(request, 'Course not found')
            else:
                messages.error(request, 'Please enter a course name')
        
        elif action == 'delete':
            # Delete course
            course_id = request.POST.get('course_id')
            
            if course_id:
                try:
                    course = Course.objects.get(id=course_id)
                    name = course.name
                    
                    # Check if course has students
                    if Student.objects.filter(course=course).exists():
                        messages.error(request, f'Cannot delete course "{name}" because it has students assigned to it.')
                    # Check if course has sections
                    elif YearSection.objects.filter(course=course).exists():
                        messages.error(request, f'Cannot delete course "{name}" because it has sections assigned to it.')
                    else:
                        # Delete course
                        course.delete()
                        messages.success(request, f'Course "{name}" deleted successfully.')
                except Course.DoesNotExist:
                    messages.error(request, 'Course not found.')
                except Exception as e:
                    messages.error(request, f'Error deleting course: {str(e)}')
            else:
                messages.error(request, 'Invalid course ID.')
        
        elif action == 'add_section':
            # Add section to course
            course_id = request.POST.get('course_id')
            year = request.POST.get('year')
            section = request.POST.get('section')
            
            if course_id and year and section:
                try:
                    # Get course
                    course = Course.objects.get(id=course_id)
                    
                    # Check if section already exists
                    if YearSection.objects.filter(course=course, year=year, section=section).exists():
                        messages.error(request, f'Section "{year}-{section}" already exists for course "{course.name}".')
                    else:
                        # Create section
                        YearSection.objects.create(
                            course=course,
                            year=year,
                            section=section
                        )
                        messages.success(request, f'Section "{year}-{section}" added to course "{course.name}" successfully.')
                except Course.DoesNotExist:
                    messages.error(request, 'Course not found.')
                except Exception as e:
                    messages.error(request, f'Error adding section: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        return redirect('dashboard:admin_courses')
    
    # Get all courses
    courses = Course.objects.all().order_by('name')
    
    # Get all departments for dropdown
    departments = Department.objects.all().order_by('name')
    
    context = {
        'courses': courses,
        'departments': departments,
    }
    
    return render(request, 'dashboard/admin/courses.html', context)

@login_required
def admin_sections(request):
    """
    Admin sections management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Handle form submissions
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'create':
            # Create new section
            course_id = request.POST.get('course')
            year = request.POST.get('year')
            section = request.POST.get('section')
            
            if course_id and year and section:
                try:
                    # Get course
                    course = Course.objects.get(id=course_id)
                    
                    # Check if section already exists
                    if YearSection.objects.filter(course=course, year=year, section=section).exists():
                        messages.error(request, f'Section "{year}-{section}" already exists for course "{course.name}".')
                    else:
                        # Create section
                        YearSection.objects.create(
                            course=course,
                            year=year,
                            section=section
                        )
                        messages.success(request, f'Section "{year}-{section}" created successfully for course "{course.name}".')
                except Course.DoesNotExist:
                    messages.error(request, 'Selected course does not exist.')
                except Exception as e:
                    messages.error(request, f'Error creating section: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'update':
            # Update existing section
            section_id = request.POST.get('section_id')
            course_id = request.POST.get('course')
            year = request.POST.get('year')
            section_name = request.POST.get('section')
            
            if section_id and course_id and year and section_name:
                try:
                    # Get section
                    year_section = YearSection.objects.get(id=section_id)
                    
                    # Get course
                    course = Course.objects.get(id=course_id)
                    
                    # Check if another section has the same details
                    if YearSection.objects.filter(
                        course=course, 
                        year=year, 
                        section=section_name
                    ).exclude(id=section_id).exists():
                        messages.error(request, f'Another section "{year}-{section_name}" already exists for course "{course.name}".')
                    else:
                        # Update section
                        year_section.course = course
                        year_section.year = year
                        year_section.section = section_name
                        year_section.save()
                        
                        messages.success(request, f'Section "{year}-{section_name}" updated successfully.')
                except YearSection.DoesNotExist:
                    messages.error(request, 'Section not found.')
                except Course.DoesNotExist:
                    messages.error(request, 'Selected course does not exist.')
                except Exception as e:
                    messages.error(request, f'Error updating section: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'delete':
            # Delete section
            section_id = request.POST.get('section_id')
            
            if section_id:
                try:
                    year_section = YearSection.objects.get(id=section_id)
                    section_name = f"{year_section.year}-{year_section.section}"
                    course_name = year_section.course.name
                    
                    # Check if section has students
                    if Student.objects.filter(year_section=year_section).exists():
                        messages.error(request, f'Cannot delete section "{section_name}" because it has students assigned to it.')
                    else:
                        # Delete section
                        year_section.delete()
                        messages.success(request, f'Section "{section_name}" from course "{course_name}" deleted successfully.')
                except YearSection.DoesNotExist:
                    messages.error(request, 'Section not found.')
                except Exception as e:
                    messages.error(request, f'Error deleting section: {str(e)}')
            else:
                messages.error(request, 'Invalid section ID.')
        
        return redirect('dashboard:admin_sections')
    
    # Get all sections with their courses
    sections = YearSection.objects.select_related('course').all().order_by('course__name', 'year', 'section')
    
    # Get all courses for the form
    courses = Course.objects.all().order_by('name')
    
    context = {
        'sections': sections,
        'courses': courses,
    }
    
    return render(request, 'dashboard/admin/sections.html', context)

@login_required
def admin_students(request):
    """
    Admin students management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Handle form submissions
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'create':
            # Create new student
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            middle_name = request.POST.get('middle_name', '')
            email = request.POST.get('email')
            contact_number = request.POST.get('contact_number', '')
            school_id = request.POST.get('school_id')
            course_id = request.POST.get('course')
            year_section_id = request.POST.get('year_section')
            semester = request.POST.get('semester', '1st_semester')
            term = request.POST.get('term', 'midterm')
            
            if first_name and last_name and email and school_id and course_id and year_section_id:
                try:
                    # Check if student with this ID already exists
                    if Student.objects.filter(school_id=school_id).exists():
                        messages.error(request, f'Student with ID {school_id} already exists.')
                    else:
                        # Get course and section
                        course = Course.objects.get(id=course_id)
                        year_section = YearSection.objects.get(id=year_section_id)
                        
                        # Get department from course
                        department = None
                        if course.department:
                            department = course.department
                            print(f"DEBUG: Assigning department {department.name} from course {course.name}")
                        
                        # Create student with QR code
                        student = Student.objects.create(
                            first_name=first_name,
                            last_name=last_name,
                            middle_name=middle_name,
                            email=email,
                            contact_number=contact_number,
                            school_id=school_id,
                            course=course,
                            department=department,  # Set department from course
                            year_section=year_section,
                            semester=semester,
                            term=term
                        )
                        
                        messages.success(request, f'Student {first_name} {last_name} created successfully.')
                        
                except Course.DoesNotExist:
                    messages.error(request, 'Selected course does not exist.')
                except YearSection.DoesNotExist:
                    messages.error(request, 'Selected year and section does not exist.')
                except Exception as e:
                    messages.error(request, f'Error creating student: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'update':
            # Update existing student
            student_id = request.POST.get('student_id')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            middle_name = request.POST.get('middle_name', '')
            email = request.POST.get('email')
            contact_number = request.POST.get('contact_number', '')
            school_id = request.POST.get('school_id')
            course_id = request.POST.get('course')
            year_section_id = request.POST.get('year_section')
            semester = request.POST.get('semester', '1st_semester')
            term = request.POST.get('term', 'midterm')
            
            if student_id and first_name and last_name and email and school_id and course_id and year_section_id:
                try:
                    # Get student
                    student = Student.objects.get(id=student_id)
                    
                    # Check if another student has this ID
                    if Student.objects.filter(school_id=school_id).exclude(id=student_id).exists():
                        messages.error(request, f'Another student with ID {school_id} already exists.')
                    else:
                        # Get course and section
                        course = Course.objects.get(id=course_id)
                        year_section = YearSection.objects.get(id=year_section_id)
                        
                        # Get department from course
                        department = None
                        if course.department:
                            department = course.department
                            print(f"DEBUG: Updating department to {department.name} from course {course.name}")
                        
                        # Update student
                        student.first_name = first_name
                        student.last_name = last_name
                        student.middle_name = middle_name
                        student.email = email
                        student.contact_number = contact_number
                        student.school_id = school_id
                        student.course = course
                        student.department = department  # Update department from course
                        student.year_section = year_section
                        student.semester = semester
                        student.term = term
                        student.save()
                        
                        messages.success(request, f'Student {first_name} {last_name} updated successfully.')
                        
                except Student.DoesNotExist:
                    messages.error(request, 'Student not found.')
                except Course.DoesNotExist:
                    messages.error(request, 'Selected course does not exist.')
                except YearSection.DoesNotExist:
                    messages.error(request, 'Selected year and section does not exist.')
                except Exception as e:
                    messages.error(request, f'Error updating student: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields.')
        
        elif action == 'delete':
            # Delete student
            student_id = request.POST.get('student_id')
            
            if student_id:
                try:
                    student = Student.objects.get(id=student_id)
                    name = f"{student.first_name} {student.last_name}"
                    
                    # Delete student
                    student.delete()
                    
                    messages.success(request, f'Student {name} deleted successfully.')
                except Student.DoesNotExist:
                    messages.error(request, 'Student not found.')
                except Exception as e:
                    messages.error(request, f'Error deleting student: {str(e)}')
            else:
                messages.error(request, 'Invalid student ID.')
        
        return redirect('dashboard:admin_students')
    
    # Get all students
    students = Student.objects.all().order_by('last_name', 'first_name')
    
    # Get courses and sections for forms
    courses = Course.objects.all().order_by('name')
    sections = YearSection.objects.all().order_by('course__name', 'year', 'section')
    
    # Debug info
    print(f"DEBUG: Found {courses.count()} courses")
    print(f"DEBUG: Found {sections.count()} sections")
    for section in sections:
        print(f"DEBUG: Section {section.year} - {section.section}, Course: {section.course.name}, ID: {section.course.id}")
    
    context = {
        'students': students,
        'courses': courses,
        'sections': sections,
    }
    
    return render(request, 'dashboard/admin/students.html', context)

@login_required
def admin_clearance(request):
    """
    Admin clearance management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'create_template':
            # Create a clearance template
            field_names = request.POST.getlist('field_name[]')
            staff_ids = request.POST.getlist('staff_assign[]')
            template_name = request.POST.get('template_name', 'Default Template')
            department_id = request.POST.get('department_id')
            year_level_target = request.POST.get('year_level_target', 'all')
            
            # Get department if provided
            department = None
            if department_id:
                try:
                    department = Department.objects.get(id=department_id)
                except Department.DoesNotExist:
                    pass
            
            print(f"DEBUG: create_template action received - template_name: {template_name}, fields: {len(field_names)}")
            
            if field_names:
                # Create fields data for template
                fields_data = []
                
                # Create template fields
                for i, field_name in enumerate(field_names):
                    if field_name.strip():
                        staff_id = None
                        if i < len(staff_ids) and staff_ids[i]:
                            staff_id = staff_ids[i]
                        
                        fields_data.append({
                            'name': field_name.strip(),
                            'staff_id': staff_id
                        })
                
                print(f"DEBUG: Template fields created: {len(fields_data)}")
                
                # Create template in database
                template = ClearanceTemplate.objects.create(
                    name=template_name,
                    fields=fields_data,
                    department=department,
                    year_level_target=year_level_target,
                    created_by=request.user,
                    is_active=True  # Set as active by default
                )
                
                # Store active template ID in session
                request.session['active_template_id'] = str(template.id)
                
                # Apply template to students without clearance forms
                try:
                    # Count students
                    student_count = Student.objects.count()
                    print(f"DEBUG: Found {student_count} students in the database")
                    
                    # Auto-update student departments based on their courses
                    update_student_departments()
                    
                    # Get all students
                    all_students = Student.objects.all()
                    for student in all_students:
                        print(f"DEBUG: Student: {student.first_name} {student.last_name}, Department: {student.department}, Year: '{student.year_section.year}'")
                    
                    # Get students without clearance forms, filtered by department and year level if applicable
                    student_query = Student.objects.all()
                    
                    # Filter by department if specified
                    if department:
                        print(f"DEBUG: Filtering by department: {department.name}")
                        # Check if any students have department set
                        has_department = Student.objects.filter(department=department).exists()
                        print(f"DEBUG: Students with department {department.name}: {Student.objects.filter(department=department).count()}")
                        if not has_department:
                            print(f"DEBUG: No students have department set to {department.name}. Checking department from course...")
                            # Try to get students by course department
                            student_query = student_query.filter(course__department=department)
                            print(f"DEBUG: Students with course department {department.name}: {student_query.count()}")
                        else:
                            student_query = student_query.filter(department=department)
                            print(f"DEBUG: After department filter, {student_query.count()} students remain")
                    
                    # Filter by year level if specified
                    if year_level_target != 'all':
                        if year_level_target == '1st_to_3rd':
                            # Filter for 1st to 3rd year students - match exact spacing from database
                            print(f"DEBUG: Filtering by year level: 1st to 3rd")
                            student_query = student_query.filter(
                                Q(year_section__year='1st Year') |
                                Q(year_section__year='2nd Year') |
                                Q(year_section__year='3rd Year')
                            )
                            print(f"DEBUG: After year level filter, {student_query.count()} students remain")
                        elif year_level_target == '4th_year':
                            # Filter for 4th year students - match exact spacing from database
                            print(f"DEBUG: Filtering by year level: 4th year")
                            student_query = student_query.filter(year_section__year='4th Year')
                            print(f"DEBUG: After year level filter, {student_query.count()} students remain")
                    
                    # Now filter out students who already have clearance forms
                    students_without_forms = student_query.exclude(id__in=ClearanceForm.objects.values_list('student_id', flat=True))
                    
                    print(f"DEBUG: Found {students_without_forms.count()} students without clearance forms matching criteria")
                    created_count = 0
                    
                    for student in students_without_forms:
                        # Create clearance form
                        clearance_form = ClearanceForm.objects.create(
                            student=student,
                            created_by=request.user
                        )
                        
                        # Create clearance fields
                        for field_data in fields_data:
                            # Check if this is a landlord field
                            if field_data['name'].lower().find('landlord') != -1 or field_data['name'].lower().find('landlady') != -1:
                                # For landlord fields, create a separate field for EACH landlord user
                                try:
                                    # Get all landlord users
                                    landlord_users = get_all_landlord_users()
                                    
                                    if landlord_users.exists():
                                        # Create a field for each landlord
                                        for landlord_user in landlord_users:
                                            ClearanceField.objects.create(
                                                form=clearance_form,
                                                name=field_data['name'],
                                                assigned_to=landlord_user,  # Assign to each landlord
                                                status=False
                                            )
                                            print(f"DEBUG: assign {landlord_user} clearance forms")
                                        print(f"DEBUG: Created landlord field for {landlord_users.count()} landlords")
                                    else:
                                        # If no landlords exist, use the admin as placeholder
                                        ClearanceField.objects.create(
                                            form=clearance_form,
                                            name=field_data['name'],
                                            assigned_to=active_template.created_by,  # Use template creator instead of request.user
                                            status=False
                                        )
                                        print(f"DEBUG: No landlords found, assigned to admin")
                                except Exception as e:
                                    print(f"ERROR creating landlord field: {str(e)}")
                                    # Fallback to admin user
                                    ClearanceField.objects.create(
                                        form=clearance_form,
                                        name=field_data['name'],
                                        assigned_to=active_template.created_by,  # Use template creator instead of request.user
                                        status=False
                                    )
                            else:
                                # For regular fields, use the assigned staff or current user
                                staff = None
                                if field_data.get('staff_id'):
                                    try:
                                        staff = User.objects.get(id=field_data['staff_id'])
                                    except User.DoesNotExist:
                                        # If assigned staff doesn't exist, use the current user as a fallback
                                        staff = request.user
                                else:
                                    # If no staff is assigned, use the current user as a fallback
                                    staff = request.user
                                
                                ClearanceField.objects.create(
                                    form=clearance_form,
                                    name=field_data['name'],
                                    assigned_to=staff,
                                    status=False
                                )
                        
                        created_count += 1
                    
                    print(f"DEBUG: Created {created_count} clearance forms")
                    
                    messages.success(request, f'Clearance template "{template_name}" created successfully and applied to {created_count} students.')
                except Exception as e:
                    print(f"ERROR: {str(e)}")
                    messages.error(request, f'Error creating clearance forms: {str(e)}')
            else:
                messages.error(request, 'Please add at least one field to the template.')
        
        elif action == 'assign_clearance':
            # Assign clearance form to students based on filters
            student_ids = request.POST.getlist('student_ids[]')
            course_id = request.POST.get('course_filter')
            year_section_id = request.POST.get('section_filter')
            department_id = request.POST.get('department_filter')
            
            # Get the active template
            active_template = ClearanceTemplate.objects.filter(is_active=True).first()
            
            if not active_template:
                messages.error(request, 'No active clearance template found. Please create and activate a template first.')
                return redirect('dashboard:admin_clearance')
            
            students_to_process = []
            
            # Get students based on filters or selected IDs
            if student_ids:
                students_to_process = Student.objects.filter(id__in=student_ids)
            elif course_id or department_id:
                query = {}
                if course_id:
                    query['course_id'] = course_id
                if year_section_id:
                    query['year_section_id'] = year_section_id
                if department_id:
                    query['department_id'] = department_id
                students_to_process = Student.objects.filter(**query)
            else:
                messages.error(request, 'Please select students or apply filters.')
                return redirect('dashboard:admin_clearance')
            
            # Create clearance forms for each student
            created_count = 0
            skipped_count = 0
            
            for student in students_to_process:
                # Check if student already has a clearance form
                if ClearanceForm.objects.filter(student=student).exists():
                    skipped_count += 1
                    continue
                
                # Create clearance form
                clearance_form = ClearanceForm.objects.create(
                    student=student,
                    created_by=request.user
                )
                
                # Create clearance fields
                for field_data in active_template.fields:
                    # Check if this is a landlord field
                    if field_data['name'].lower().find('landlord') != -1 or field_data['name'].lower().find('landlady') != -1:
                        # For landlord fields, we'll create a special field that can be signed by any landlord
                        # Get or create a special placeholder user for landlord fields
                        try:
                            # Try to find a landlord user to use as placeholder
                            landlord_user = User.objects.filter(profile__is_landlord=True).first()
                            if not landlord_user:
                                # If no landlord exists, use the admin as placeholder
                                landlord_user = request.user
                            
                            ClearanceField.objects.create(
                                form=clearance_form,
                                name=field_data['name'],
                                assigned_to=landlord_user,  # Placeholder landlord user
                                status=False
                            )
                        except Exception as e:
                            print(f"ERROR creating landlord field: {str(e)}")
                            # Fallback to admin user
                            ClearanceField.objects.create(
                                form=clearance_form,
                                name=field_data['name'],
                                assigned_to=active_template.created_by,  # Use template creator instead of request.user
                                status=False
                            )
                    else:
                        # For regular fields, use the assigned staff or current user
                        staff = None
                        if field_data.get('staff_id'):
                            try:
                                staff = User.objects.get(id=field_data['staff_id'])
                            except User.DoesNotExist:
                                # If assigned staff doesn't exist, use the current user as a fallback
                                staff = request.user
                        else:
                            # If no staff is assigned, use the current user as a fallback
                            staff = request.user
                        
                        ClearanceField.objects.create(
                            form=clearance_form,
                            name=field_data['name'],
                            assigned_to=staff,
                            status=False
                        )
                
                created_count += 1
            
            if created_count > 0:
                messages.success(request, f'Created clearance forms for {created_count} students.')
            if skipped_count > 0:
                messages.info(request, f'Skipped {skipped_count} students who already have clearance forms.')
        
        elif action == 'delete':
            clearance_id = request.POST.get('clearance_id')
            
            if clearance_id:
                try:
                    clearance_form = ClearanceForm.objects.get(id=clearance_id)
                    student_name = f"{clearance_form.student.first_name} {clearance_form.student.last_name}"
                    
                    # Delete clearance fields first
                    ClearanceField.objects.filter(form=clearance_form).delete()
                    
                    # Delete the clearance form
                    clearance_form.delete()
                    
                    messages.success(request, f'Clearance form for {student_name} has been deleted.')
                except ClearanceForm.DoesNotExist:
                    messages.error(request, 'Clearance form not found.')
            else:
                messages.error(request, 'Invalid clearance form ID.')
                
        return redirect('dashboard:admin_clearance')
    
    # Get filter parameters
    department_id = request.GET.get('department')
    
    # Get all clearance forms
    clearance_forms_query = ClearanceForm.objects.all().order_by('-created_at')
    
    # Apply department filter if provided
    if department_id:
        try:
            department_id = int(department_id)
            clearance_forms_query = clearance_forms_query.filter(student__department_id=department_id)
        except (ValueError, TypeError):
            pass
    
    # Get all departments for the filter dropdown
    departments = Department.objects.all().order_by('name')
    
    # Get all clearance forms with annotations
    clearance_forms = []
    
    for form in clearance_forms_query:
        # Get all fields
        all_fields = form.fields.all()
        
        # Find landlord/landlady fields
        landlord_fields = all_fields.filter(
            Q(name__icontains='landlord') | Q(name__icontains='landlady')
        )
        
        # Get non-landlord fields
        non_landlord_fields = all_fields.exclude(
            Q(name__icontains='landlord') | Q(name__icontains='landlady')
        )
        
        # Calculate correct total fields (count landlord fields as one)
        total_fields = non_landlord_fields.count()
        if landlord_fields.exists():
            total_fields += 1  # Only count landlord fields as one field
        
        # Calculate signed fields (for landlord fields, count as signed if any one is signed)
        signed_fields = non_landlord_fields.filter(status=True).count()
        if landlord_fields.filter(status=True).exists():
            signed_fields += 1  # Count as signed if any landlord field is signed
        
        progress_percentage = 0
        if total_fields > 0:
            progress_percentage = (signed_fields / total_fields) * 100
        
        form.total_fields = total_fields
        form.signed_fields = signed_fields
        form.progress_percentage = progress_percentage
        clearance_forms.append(form)
    
    # Get all students
    students = Student.objects.all().order_by('last_name')
    
    # Get all courses and sections for filtering
    courses = Course.objects.all()
    sections = YearSection.objects.all()
    
    # Get all departments for filtering
    departments = Department.objects.all().order_by('name')
    
    # Get all staff users for field assignment (exclude landlords)
    staff_users = User.objects.filter(
        Q(profile__user_type='staff') | Q(profile__user_type='admin') | Q(profile__user_type='user'),
        ~Q(profile__is_landlord=True)  # Exclude landlord users
    ).order_by('last_name')
    
    # Get landlord users separately
    landlord_users = User.objects.filter(
        profile__is_landlord=True
    ).order_by('last_name')
    
    # Get active template
    active_template = ClearanceTemplate.objects.filter(is_active=True).first()
    
    context = {
        'clearance_forms': clearance_forms,
        'students': students,
        'staff_users': staff_users,
        'landlord_users': landlord_users,
        'courses': courses,
        'sections': sections,
        'departments': departments,
        'template_data': active_template
    }
    
    return render(request, 'dashboard/admin/clearance.html', context)

@login_required
def admin_reports(request):
    """
    Admin reports management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Get all submitted reports
    reports = DailyReport.objects.filter(
        submitted=True
    ).order_by('-date')
    
    # Calculate total fields and get course IDs for each report
    for report in reports:
        total_fields = 0
        student_courses = set()
        
        for entry in report.entries.values():
            total_fields += len(entry.get('fields', []))
            student_id = entry.get('student_id')
            if student_id:
                try:
                    student = Student.objects.get(id=student_id)
                    if student.course:
                        student_courses.add(str(student.course.id))
                except Student.DoesNotExist:
                    pass
                
        report.total_fields = total_fields
        report.student_courses = list(student_courses)
    
    # Get all courses for filtering
    courses = Course.objects.all()
    
    # Get all staff users
    staff_users = User.objects.filter(
        profile__user_type='staff'
    )
    
    context = {
        'reports': reports,
        'courses': courses,
        'staff_users': staff_users
    }
    
    return render(request, 'dashboard/admin/reports.html', context)

# Staff dashboard views
@login_required
def staff_dashboard(request):
    """
    Staff dashboard view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if hasattr(request.user, 'profile') and request.user.profile.user_type == 'user':
        return redirect('dashboard:user_dashboard')
    
    # Get assigned clearance fields
    assigned_fields = ClearanceField.objects.filter(assigned_to=request.user)
    
    context = {
        'assigned_fields': assigned_fields,
    }
    
    return render(request, 'dashboard/staff/dashboard.html', context)

@login_required
def user_dashboard(request):
    """
    User dashboard view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if hasattr(request.user, 'profile') and request.user.profile.user_type != 'user':
        return redirect('dashboard:staff_dashboard')
    
    # Get assigned clearance fields
    assigned_fields = ClearanceField.objects.filter(assigned_to=request.user)
    
    # Check if user is a landlord
    is_landlord = hasattr(request.user, 'profile') and request.user.profile.is_landlord
    
    context = {
        'assigned_fields': assigned_fields,
        'is_landlord': is_landlord,
    }
    
    return render(request, 'dashboard/user/dashboard.html', context)

@login_required
def user_signature(request):
    """
    User signature management view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if hasattr(request.user, 'profile') and request.user.profile.user_type != 'user':
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'draw':
            # Handle signature drawn on canvas
            signature_data = request.POST.get('signature_data', '')
            if signature_data:
                # Convert base64 data to file
                format, imgstr = signature_data.split(';base64,')
                ext = format.split('/')[-1]
                signature_file = ContentFile(base64.b64decode(imgstr), name=f"signature_{request.user.username}.{ext}")
                
                # Save to user profile
                request.user.profile.signature = signature_file
                request.user.profile.save()
                
                messages.success(request, 'Signature saved successfully.')
            else:
                messages.error(request, 'Invalid signature data.')
        
        elif action == 'upload':
            # Handle uploaded signature image
            if 'signature' in request.FILES:
                signature_file = request.FILES['signature']
                
                # Validate file type
                valid_extensions = ['.jpg', '.jpeg', '.png']
                ext = os.path.splitext(signature_file.name)[1].lower()
                
                if ext not in valid_extensions:
                    messages.error(request, 'Invalid file format. Please upload a JPG or PNG image.')
                else:
                    # Save to user profile
                    request.user.profile.signature = signature_file
                    request.user.profile.save()
                    
                    messages.success(request, 'Signature uploaded successfully.')
            else:
                messages.error(request, 'No signature file uploaded.')
        
        return redirect('dashboard:user_signature')
    
    context = {}
    
    return render(request, 'dashboard/user/signature.html', context)

@login_required
def user_scan(request):
    """
    User QR code scanning view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if hasattr(request.user, 'profile') and request.user.profile.user_type != 'user':
        return redirect('dashboard:staff_dashboard')
    
    # Check if user has a signature
    if not request.user.profile.signature:
        messages.warning(request, 'You need to set up your signature before you can sign clearance forms.')
        return redirect('dashboard:user_signature')
    
    # Check if user is a landlord
    is_landlord = hasattr(request.user, 'profile') and request.user.profile.is_landlord
    
    # Get assigned clearance fields (only for regular users, not landlords)
    assigned_fields = []
    if not is_landlord:
        assigned_fields = ClearanceField.objects.filter(
            assigned_to=request.user,
            status=False
        ).select_related('form', 'form__student')
    
    # Process scan
    if request.method == 'POST':
        token = request.POST.get('token')
        field_id = request.POST.get('field_id')
        
        if token:
            try:
                # Get student by token
                student = Student.objects.get(token=token)
                
                # Handle landlord signature differently
                if is_landlord:
                    # Check if landlord has already signed for this student
                    existing_signature = LandlordSignature.objects.filter(
                        student=student
                    ).exists()
                    
                    if existing_signature:
                        messages.warning(request, f'A landlord has already signed for {student.first_name} {student.last_name}.')
                    else:
                        # Create landlord signature
                        LandlordSignature.objects.create(
                            student=student,
                            landlord=request.user,
                            signature=request.user.profile.signature,
                            signed_by_name=request.user.get_full_name() or request.user.username
                        )
                        
                        # Find and sign any landlord fields in the student's clearance form
                        try:
                            clearance_form = ClearanceForm.objects.get(student=student)
                            landlord_fields = clearance_form.fields.filter(
                                name__icontains='landlord',
                                status=False
                            )
                            
                            for field in landlord_fields:
                                field.signature = request.user.profile.signature
                                field.signed_at = timezone.localtime()
                                field.status = True
                                field.signed_by_name = request.user.get_full_name() or request.user.username
                                field.save()
                            
                            messages.success(request, f'Successfully signed as Landlord/Landlady for {student.first_name} {student.last_name}')
                            
                            # Check if all fields are signed
                            all_signed = not ClearanceField.objects.filter(
                                form=clearance_form,
                                status=False
                            ).exists()
                            
                            if all_signed:
                                # Update clearance form status
                                clearance_form.status = 'complete'
                                clearance_form.save()
                                
                                # Update student clearance status
                                student.clearance_status = 'complete'
                                student.save()
                                
                                messages.info(request, f'All clearance fields for {student.first_name} {student.last_name} have been signed!')
                                
                        except ClearanceForm.DoesNotExist:
                            messages.warning(request, f'Student {student.first_name} {student.last_name} does not have a clearance form yet.')
                
                # Regular user (non-landlord) handling
                elif request.user.profile.auto_sign:
                    # Get all assigned fields for this student
                    fields_to_sign = ClearanceField.objects.filter(
                        form__student=student,
                        assigned_to=request.user,
                        status=False
                    )
                    
                    if not fields_to_sign.exists():
                        messages.info(request, f'No pending fields to sign for {student.first_name} {student.last_name}')
                        return redirect('dashboard:user_scan')
                    
                    # Sign all fields
                    signed_count = 0
                    for field in fields_to_sign:
                        # Sign the clearance field
                        field.signature = request.user.profile.signature
                        field.signed_at = timezone.localtime()
                        field.status = True
                        field.signed_by_name = request.user.get_full_name() or request.user.username
                        field.save()
                        signed_count += 1
                        
                        # Update daily report - ensure we're using today's report
                        today = timezone.localtime().date()
                        
                        # Get or create today's report
                        try:
                            report = DailyReport.objects.get(
                                staff=request.user,
                                date=today
                            )
                            
                            # If today's report was already submitted, create a new one
                            if report.submitted:
                                # Show warning about the submitted report
                                if signed_count == 1:  # Only show once
                                    messages.warning(request, f'Your daily report for today was already submitted. Creating a new report for additional signatures.')
                                
                                # Create a new report for today's additional signatures
                                report = DailyReport.objects.create(
                                    staff=request.user,
                                    date=today,
                                    entries={},
                                    submitted=False
                                )
                        except DailyReport.DoesNotExist:
                            # Create a new report for today
                            report = DailyReport.objects.create(
                                staff=request.user,
                                date=today,
                                entries={},
                                submitted=False
                            )
                        
                        # Add entry to the report
                        entries = report.entries or {}
                        student_id = str(student.id)
                        
                        if student_id in entries:
                            # Update existing entry
                            if field.name not in entries[student_id]['fields']:
                                entries[student_id]['fields'].append(field.name)
                        else:
                            # Create new entry
                            entries[student_id] = {
                                'student_id': str(student.id),
                                'school_id': student.school_id,
                                'student_name': f"{student.first_name} {student.last_name}",
                                'course': student.course.name if student.course else '',
                                'fields': [field.name],
                                'time': timezone.localtime().strftime('%H:%M')
                            }
                        
                        report.entries = entries
                        report.save()
                    
                    messages.success(request, f'Successfully signed {signed_count} fields for {student.first_name} {student.last_name}')
                    
                    # Check if all fields are signed
                    all_signed = not ClearanceField.objects.filter(
                        form__student=student,
                        status=False
                    ).exists()
                    
                    if all_signed:
                        # Update clearance form status
                        clearance_form = ClearanceForm.objects.get(student=student)
                        clearance_form.status = 'complete'
                        clearance_form.save()
                        
                        # Update student clearance status
                        student.clearance_status = 'complete'
                        student.save()
                        
                        messages.info(request, f'All clearance fields for {student.first_name} {student.last_name} have been signed!')
                
                elif field_id:
                    # Get clearance field
                    field = ClearanceField.objects.get(id=field_id, assigned_to=request.user, status=False)
                    
                    # Ensure user has a signature
                    if not request.user.profile.signature:
                        messages.error(request, 'You need to set up your signature before you can sign clearance forms.')
                        return redirect('dashboard:user_signature')
                    
                    # Sign the clearance field
                    field.signature = request.user.profile.signature
                    field.signed_at = timezone.localtime()
                    field.status = True
                    field.signed_by_name = request.user.get_full_name() or request.user.username
                    field.save()
                    
                    # Update daily report - ensure we're using today's report
                    today = timezone.localtime().date()
                    
                    # Get or create today's report
                    try:
                        report = DailyReport.objects.get(
                            staff=request.user,
                            date=today
                        )
                        
                        # If today's report was already submitted, create a new one
                        if report.submitted:
                            # Show warning about the submitted report
                            messages.warning(request, f'Your daily report for today was already submitted. Creating a new report for additional signatures.')
                            
                            # Create a new report for today's additional signatures
                            report = DailyReport.objects.create(
                                staff=request.user,
                                date=today,
                                entries={},
                                submitted=False
                            )
                    except DailyReport.DoesNotExist:
                        # Create a new report for today
                        report = DailyReport.objects.create(
                            staff=request.user,
                            date=today,
                            entries={},
                            submitted=False
                        )
                    
                    # Add entry to the report
                    entries = report.entries or {}
                    student_id = str(student.id)
                    
                    if student_id in entries:
                        # Update existing entry
                        if field.name not in entries[student_id]['fields']:
                            entries[student_id]['fields'].append(field.name)
                    else:
                        # Create new entry
                        entries[student_id] = {
                            'student_id': str(student.id),
                            'school_id': student.school_id,
                            'student_name': f"{student.first_name} {student.last_name}",
                            'course': student.course.name if student.course else '',
                            'fields': [field.name],
                            'time': timezone.localtime().strftime('%H:%M')
                        }
                    
                    report.entries = entries
                    report.save()
                    
                    messages.success(request, f'Successfully signed {field.name} for {student.first_name} {student.last_name}')
                    
                    # Check if all fields are signed
                    all_signed = not ClearanceField.objects.filter(
                        form=field.form,
                        status=False
                    ).exists()
                    
                    if all_signed:
                        # Update clearance form status
                        field.form.status = 'complete'
                        field.form.save()
                        
                        # Update student clearance status
                        student.clearance_status = 'complete'
                        student.save()
                        
                        messages.info(request, f'All clearance fields for {student.first_name} {student.last_name} have been signed!')
                else:
                    # No field_id provided in manual mode
                    messages.error(request, 'Please select a field to sign.')
                    
            except Student.DoesNotExist:
                messages.error(request, 'Invalid student QR code.')
            except ClearanceField.DoesNotExist:
                messages.error(request, 'Invalid clearance field or you are not authorized to sign this field.')
        else:
            messages.error(request, 'Invalid request. Please scan a valid QR code.')
        
        return redirect('dashboard:user_scan')
    
    # Group assigned fields by student (only for regular users)
    student_fields = {}
    if not is_landlord:
        for field in assigned_fields:
            student = field.form.student
            student_id = str(student.id)
            
            if student_id not in student_fields:
                student_fields[student_id] = {
                    'student': student,
                    'fields': []
                }
            
            student_fields[student_id]['fields'].append(field)
    
    context = {
        'student_fields': student_fields,
        'has_signature': bool(request.user.profile.signature),
        'is_landlord': is_landlord,
        'auto_sign': request.user.profile.auto_sign
    }
    
    return render(request, 'dashboard/user/scan.html', context)

@login_required
def landlord_signatures(request):
    """
    View for landlords to see their signatures
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if hasattr(request.user, 'profile') and request.user.profile.user_type != 'user':
        return redirect('dashboard:staff_dashboard')
    
    # Check if user is a landlord
    is_landlord = hasattr(request.user, 'profile') and request.user.profile.is_landlord
    
    if not is_landlord:
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('dashboard:user_dashboard')
    
    # Get all signatures by this landlord
    signatures = LandlordSignature.objects.filter(
        landlord=request.user
    ).select_related('student').order_by('-created_at')
    
    context = {
        'signatures': signatures,
        'is_landlord': True
    }
    
    return render(request, 'dashboard/user/landlord_signatures.html', context)

@login_required
def staff_signature(request):
    """
    Staff signature management view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'draw':
            # Handle signature drawn on canvas
            signature_data = request.POST.get('signature_data', '')
            if signature_data:
                # Convert base64 data to file
                format, imgstr = signature_data.split(';base64,')
                ext = format.split('/')[-1]
                signature_file = ContentFile(base64.b64decode(imgstr), name=f"signature_{request.user.username}.{ext}")
                
                # Save to user profile
                request.user.profile.signature = signature_file
                request.user.profile.save()
                
                messages.success(request, 'Signature saved successfully.')
            else:
                messages.error(request, 'Invalid signature data.')
        
        elif action == 'upload':
            # Handle uploaded signature image
            if 'signature' in request.FILES:
                signature_file = request.FILES['signature']
                
                # Validate file type
                valid_extensions = ['.jpg', '.jpeg', '.png']
                ext = os.path.splitext(signature_file.name)[1].lower()
                
                if ext not in valid_extensions:
                    messages.error(request, 'Invalid file format. Please upload a JPG or PNG image.')
                else:
                    # Save to user profile
                    request.user.profile.signature = signature_file
                    request.user.profile.save()
                    
                    messages.success(request, 'Signature uploaded successfully.')
            else:
                messages.error(request, 'No signature file uploaded.')
        
        return redirect('dashboard:staff_signature')
    
    context = {}
    
    return render(request, 'dashboard/staff/signature.html', context)

@login_required
def staff_scan(request):
    """
    Staff QR code scanning view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    # Check if staff has a signature
    if not request.user.profile.signature:
        messages.warning(request, 'You need to set up your signature before you can sign clearance forms.')
        return redirect('dashboard:staff_signature')
    
    # Get assigned clearance fields
    assigned_fields = ClearanceField.objects.filter(
        assigned_to=request.user,
        status=False
    ).select_related('form', 'form__student')
    
    # Process scan
    if request.method == 'POST':
        token = request.POST.get('token')
        field_id = request.POST.get('field_id')
        
        if token:
            try:
                # Get student by token
                student = Student.objects.get(token=token)
                
                # Check if auto-sign is enabled
                if request.user.profile.auto_sign:
                    # Get all assigned fields for this student
                    fields_to_sign = ClearanceField.objects.filter(
                        form__student=student,
                        assigned_to=request.user,
                        status=False
                    )
                    
                    if not fields_to_sign.exists():
                        messages.info(request, f'No pending fields to sign for {student.first_name} {student.last_name}')
                        return redirect('dashboard:staff_scan')
                    
                    # Sign all fields
                    signed_count = 0
                    for field in fields_to_sign:
                        # Sign the clearance field
                        field.signature = request.user.profile.signature
                        field.signed_at = timezone.localtime()
                        field.status = True
                        field.signed_by_name = request.user.get_full_name() or request.user.username
                        field.save()
                        signed_count += 1
                        
                        # Update daily report - ensure we're using today's report
                        today = timezone.localtime().date()
                        
                        # Get or create today's report
                        try:
                            report = DailyReport.objects.get(
                                staff=request.user,
                                date=today
                            )
                            
                            # If today's report was already submitted, create a new one
                            if report.submitted:
                                # Show warning about the submitted report
                                if signed_count == 1:  # Only show once
                                    messages.warning(request, f'Your daily report for today was already submitted. Creating a new report for additional signatures.')
                                
                                # Create a new report for today's additional signatures
                                report = DailyReport.objects.create(
                                    staff=request.user,
                                    date=today,
                                    entries={},
                                    submitted=False
                                )
                        except DailyReport.DoesNotExist:
                            # Create a new report for today
                            report = DailyReport.objects.create(
                                staff=request.user,
                                date=today,
                                entries={},
                                submitted=False
                            )
                        
                        # Add entry to the report
                        entries = report.entries or {}
                        student_id = str(student.id)
                        
                        if student_id in entries:
                            # Update existing entry
                            if field.name not in entries[student_id]['fields']:
                                entries[student_id]['fields'].append(field.name)
                        else:
                            # Create new entry
                            entries[student_id] = {
                                'student_id': str(student.id),
                                'school_id': student.school_id,
                                'student_name': f"{student.first_name} {student.last_name}",
                                'course': student.course.name if student.course else '',
                                'fields': [field.name],
                                'time': timezone.localtime().strftime('%H:%M')
                            }
                        
                        report.entries = entries
                        report.save()
                    
                    messages.success(request, f'Successfully signed {signed_count} fields for {student.first_name} {student.last_name}')
                    
                    # Check if all fields are signed
                    all_signed = not ClearanceField.objects.filter(
                        form__student=student,
                        status=False
                    ).exists()
                    
                    if all_signed:
                        # Update clearance form status
                        clearance_form = ClearanceForm.objects.get(student=student)
                        clearance_form.status = 'complete'
                        clearance_form.save()
                        
                        # Update student clearance status
                        student.clearance_status = 'complete'
                        student.save()
                        
                        messages.info(request, f'All clearance fields for {student.first_name} {student.last_name} have been signed!')
                
                elif field_id:
                    # Get clearance field
                    field = ClearanceField.objects.get(id=field_id, assigned_to=request.user, status=False)
                    
                    # Ensure staff has a signature
                    if not request.user.profile.signature:
                        messages.error(request, 'You need to set up your signature before you can sign clearance forms.')
                        return redirect('dashboard:staff_signature')
                    
                    # Sign the clearance field
                    field.signature = request.user.profile.signature
                    field.signed_at = timezone.localtime()
                    field.status = True
                    field.signed_by_name = request.user.get_full_name() or request.user.username
                    field.save()
                    
                    # Update daily report - ensure we're using today's report
                    today = timezone.localtime().date()
                    
                    # Get or create today's report
                    try:
                        report = DailyReport.objects.get(
                            staff=request.user,
                            date=today
                        )
                        
                        # If today's report was already submitted, create a new one
                        if report.submitted:
                            # Show warning about the submitted report
                            messages.warning(request, f'Your daily report for today was already submitted. Creating a new report for additional signatures.')
                            
                            # Create a new report for today's additional signatures
                            report = DailyReport.objects.create(
                                staff=request.user,
                                date=today,
                                entries={},
                                submitted=False
                            )
                    except DailyReport.DoesNotExist:
                        # Create a new report for today
                        report = DailyReport.objects.create(
                            staff=request.user,
                            date=today,
                            entries={},
                            submitted=False
                        )
                    
                    # Add entry to the report
                    entries = report.entries or {}
                    student_id = str(student.id)
                    
                    if student_id in entries:
                        # Update existing entry
                        if field.name not in entries[student_id]['fields']:
                            entries[student_id]['fields'].append(field.name)
                    else:
                        # Create new entry
                        entries[student_id] = {
                            'student_id': str(student.id),
                            'school_id': student.school_id,
                            'student_name': f"{student.first_name} {student.last_name}",
                            'course': student.course.name if student.course else '',
                            'fields': [field.name],
                            'time': timezone.localtime().strftime('%H:%M')
                        }
                    
                    report.entries = entries
                    report.save()
                    
                    messages.success(request, f'Successfully signed {field.name} for {student.first_name} {student.last_name}')
                    
                    # Check if all fields are signed
                    all_signed = not ClearanceField.objects.filter(
                        form=field.form,
                        status=False
                    ).exists()
                    
                    if all_signed:
                        # Update clearance form status
                        field.form.status = 'complete'
                        field.form.save()
                        
                        # Update student clearance status
                        student.clearance_status = 'complete'
                        student.save()
                        
                        messages.info(request, f'All clearance fields for {student.first_name} {student.last_name} have been signed!')
                else:
                    # No field_id provided in manual mode
                    messages.error(request, 'Please select a field to sign.')
                
            except Student.DoesNotExist:
                messages.error(request, 'Invalid student QR code.')
            except ClearanceField.DoesNotExist:
                messages.error(request, 'Invalid clearance field or you are not authorized to sign this field.')
        else:
            messages.error(request, 'Invalid request. Please scan a valid QR code.')
        
        return redirect('dashboard:staff_scan')
    
    # Group assigned fields by student
    student_fields = {}
    for field in assigned_fields:
        student = field.form.student
        student_id = str(student.id)
        
        if student_id not in student_fields:
            student_fields[student_id] = {
                'student': student,
                'fields': []
            }
        
        student_fields[student_id]['fields'].append(field)
    
    context = {
        'student_fields': student_fields,
        'has_signature': bool(request.user.profile.signature),
        'auto_sign': request.user.profile.auto_sign
    }
    
    return render(request, 'dashboard/staff/scan.html', context)

@login_required
def staff_reports(request):
    """
    Staff daily reports view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    # Get today's date
    today = timezone.localtime().date()
    
    # First check if there's an existing report for today
    try:
        report = DailyReport.objects.get(
            staff=request.user,
            date=today
        )
    except DailyReport.DoesNotExist:
        # If no report exists for today, check if there are any old unsubmitted reports
        old_reports = DailyReport.objects.filter(
            staff=request.user,
            date__lt=today,
            submitted=False
        )
        
        # Auto-submit old reports or delete them
        for old_report in old_reports:
            # Only save reports that have entries
            if old_report.entries and len(old_report.entries) > 0:
                old_report.submitted = True
                old_report.save()
                messages.info(
                    request, 
                    f'Your previous unsubmitted report from {old_report.date.strftime("%B %d, %Y")} was automatically submitted.'
                )
            else:
                # Delete empty reports
                old_report.delete()
        
        # Create a new report for today
        report = DailyReport.objects.create(
        staff=request.user,
        date=today,
            entries={},
            submitted=False
    )
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'submit' and not report.submitted:
            # Submit the report
            report.submitted = True
            report.save()
            
            messages.success(request, 'Daily report submitted successfully.')
        
        return redirect('dashboard:staff_reports')
    
    # Count total fields signed
    total_fields = 0
    for entry in report.entries.values():
        total_fields += len(entry.get('fields', []))
    
    context = {
        'report': report,
        'total_fields': total_fields
    }
    
    return render(request, 'dashboard/staff/reports.html', context)

@login_required
def staff_history(request):
    """
    Staff report history view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    # Get all submitted reports for this staff
    reports = DailyReport.objects.filter(
        staff=request.user,
        submitted=True
    ).order_by('-date')
    
    # Calculate total fields for each report
    for report in reports:
        total_fields = 0
        for entry in report.entries.values():
            total_fields += len(entry.get('fields', []))
        report.total_fields = total_fields
    
    context = {
        'reports': reports
    }
    
    return render(request, 'dashboard/staff/history.html', context)

@login_required
def get_student(request):
    """
    API endpoint to get student details by ID
    """
    student_id = request.GET.get('id')
    
    if not student_id:
        return JsonResponse({'success': False, 'error': 'Student ID is required'})
    
    try:
        student = Student.objects.get(id=student_id)
        
        data = {
            'success': True,
            'student': {
                'id': student.school_id,
                'name': f"{student.first_name} {student.last_name}",
                'email': student.email,
                'course': student.course.name if student.course else '',
                'section': student.section.name if student.section else '',
                'semester': student.semester,
            }
        }
        
        return JsonResponse(data)
    
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'})


@login_required
def verify_qr(request):
    """
    API endpoint to verify QR code and get student details
    """
    token = request.GET.get('token')
    
    if not token:
        return JsonResponse({'success': False, 'error': 'QR code token is required'})
    
    try:
        student = Student.objects.get(token=token)
        
        # Check if student has a clearance form
        try:
            clearance_form = ClearanceForm.objects.get(student=student)
            
            # Get all fields and check which ones are signed
            all_fields = clearance_form.fields.all()
            total_fields = all_fields.count()
            signed_fields = all_fields.filter(status=True).count()
            
            # Check if user is a landlord
            is_landlord = hasattr(request.user, 'profile') and request.user.profile.is_landlord
            
            # Get fields assigned to the current user
            if is_landlord:
                # For landlords, get all landlord fields that haven't been signed yet
                assigned_fields = clearance_form.fields.filter(
                    name__icontains='landlord',
                    status=False
                )
                
                # Check if a landlord has already signed for this student
                already_signed = LandlordSignature.objects.filter(student=student).exists()
                if already_signed:
                    assigned_fields = []
            else:
                # For regular users, get only fields specifically assigned to them
                assigned_fields = ClearanceField.objects.filter(
                    form=clearance_form,
                    assigned_to=request.user,
                    status=False
                )
            
            fields_data = []
            for field in assigned_fields:
                fields_data.append({
                    'id': field.id,
                    'name': field.name
                })
            
            data = {
                'success': True,
                'student': {
                    'id': student.school_id,
                    'name': f"{student.first_name} {student.last_name}",
                    'course': student.course.name if student.course else '',
                    'section': f"{student.year_section.year} - {student.year_section.section}" if student.year_section else '',
                    'email': student.email,
                    'semester': student.get_semester_display(),
                },
                'clearance': {
                    'id': clearance_form.id,
                    'status': clearance_form.status,
                    'created_at': clearance_form.created_at.strftime('%Y-%m-%d'),
                    'total_fields': total_fields,
                    'signed_fields': signed_fields,
                    'progress': int((signed_fields / total_fields) * 100) if total_fields > 0 else 0
                },
                'has_clearance': True,
                'assigned_fields': fields_data,
                'all_signed': len(fields_data) == 0
            }
            
            return JsonResponse(data)
            
        except ClearanceForm.DoesNotExist:
            return JsonResponse({
                'success': True,
                'student': {
                    'id': student.school_id,
                    'name': f"{student.first_name} {student.last_name}",
                    'course': student.course.name if student.course else '',
                    'section': f"{student.year_section.year} - {student.year_section.section}" if student.year_section else '',
                    'email': student.email,
                    'semester': student.get_semester_display(),
                },
                'has_clearance': False,
                'error': 'This student does not have a clearance form yet.'
            })
    
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Invalid QR code. No student found with this ID.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error: {str(e)}'})


@login_required
def staff_profile(request):
    """
    Staff profile view
    """
    if request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:admin_dashboard')
    
    if request.method == 'POST':
        # Update profile
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')
        
        if first_name and last_name and email:
            request.user.first_name = first_name
            request.user.last_name = last_name
            request.user.email = email
            request.user.save()
            
            messages.success(request, 'Profile updated successfully.')
        else:
            messages.error(request, 'Please fill in all required fields.')
        
        return redirect('dashboard:staff_profile')
    
    context = {}
    
    return render(request, 'accounts/profile.html', context)

@login_required
def import_students(request):
    """
    Import students from CSV or JSON data
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Handle JSON data import (from preview)
    if request.method == 'POST' and request.POST.get('action') == 'import_preview':
        try:
            raw_data = request.POST.get('data', '[]')
            print(f"DEBUG: Received raw data: {raw_data[:100]}...")  # Print first 100 chars for debugging
            data = json.loads(raw_data)
            
            if not data:
                return JsonResponse({'success': False, 'error': 'No data to import'})
            
            # Process data
            success_count = 0
            error_count = 0
            error_messages = []
            
            for i, row in enumerate(data):
                # Check for required fields
                required_fields = ['first_name', 'last_name', 'email', 'school_id', 'course', 'year', 'section']
                missing_fields = []
                
                for field in required_fields:
                    if field not in row or not row[field].strip():
                        missing_fields.append(field)
                
                if missing_fields:
                    error_count += 1
                    error_messages.append(f"Row {i+1}: Missing required fields: {', '.join(missing_fields)}")
                    continue
                
                try:
                    with transaction.atomic():
                        # Get or create department if provided
                        department = None
                        if 'department' in row and row['department'].strip():
                            department_name = row['department'].strip()
                            department, created = Department.objects.get_or_create(name=department_name)
                            print(f"DEBUG: Department {department_name} {'created' if created else 'found'}")
                        
                        # Get or create course
                        course_name = row['course'].strip()
                        course, created = Course.objects.get_or_create(name=course_name)
                        
                        # If department is provided, associate it with the course
                        if department and not course.department:
                            course.department = department
                            course.save()
                        
                        print(f"DEBUG: Course {course_name} {'created' if created else 'found'}")
                        
                        # Get or create year-section
                        year = row['year'].strip()
                        section = row['section'].strip()
                        year_section, created = YearSection.objects.get_or_create(
                            course=course, 
                            year=year, 
                            section=section
                        )
                        print(f"DEBUG: Year-Section {year}-{section} {'created' if created else 'found'}")
                        
                        # Check if student with same school_id exists
                        school_id = row['school_id'].strip()
                        if Student.objects.filter(school_id=school_id).exists():
                            error_count += 1
                            error_messages.append(f"Row {i+1}: Student with ID {school_id} already exists")
                            continue
                        
                        # Create student
                        student = Student.objects.create(
                            first_name=row['first_name'].strip(),
                            last_name=row['last_name'].strip(),
                            middle_name=row.get('middle_name', '').strip(),
                            email=row['email'].strip(),
                            contact_number=row.get('contact_number', '').strip(),
                            school_id=school_id,
                            department=department,
                            course=course,
                            year_section=year_section,
                            semester=row.get('semester', '1st_semester').strip(),
                            term=row.get('term', 'midterm').strip()
                        )
                        
                        success_count += 1
                except Exception as e:
                    error_count += 1
                    error_messages.append(f"Row {i+1}: {str(e)}")
            
            # Log the response we're sending back
            response_data = {
                'success': True,
                'message': f'Successfully imported {success_count} students',
                'success_count': success_count,
                'error_count': error_count,
                'errors': error_messages[:5] if error_messages else []  # Show first 5 errors
            }
            print(f"DEBUG: Import response: {response_data}")
            return JsonResponse(response_data)
        except json.JSONDecodeError as e:
            print(f"ERROR: JSON decode error - {str(e)}")
            return JsonResponse({'success': False, 'error': f'Invalid JSON data: {str(e)}'})
        except Exception as e:
            print(f"ERROR: Unexpected error during import - {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)})
    
    # Handle CSV file upload
    elif request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']
        
        # Check if file is CSV
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file')
            return redirect('dashboard:admin_students')
        
        # Try to decode the file
        try:
            csv_data = csv_file.read().decode('utf-8')
        except UnicodeDecodeError:
            messages.error(request, 'Please upload a valid CSV file with UTF-8 encoding')
            return redirect('dashboard:admin_students')
        
        # Process CSV data
        csv_io = io.StringIO(csv_data)
        reader = csv.DictReader(csv_io)
        
        # Verify we have headers
        if not reader.fieldnames:
            messages.error(request, 'CSV file has no headers')
            return redirect('dashboard:admin_students')
            
        print(f"DEBUG: CSV headers: {reader.fieldnames}")
        
        # Get required fields
        required_fields = ['first_name', 'last_name', 'email', 'school_id', 'course', 'year', 'section']
        for field in required_fields:
            if field not in reader.fieldnames:
                messages.error(request, f'Missing required field: {field}')
                return redirect('dashboard:admin_students')
        
        # Process rows
        success_count = 0
        error_count = 0
        error_messages = []
        
        for row in reader:
            try:
                with transaction.atomic():
                    # Get or create department if provided
                    department = None
                    if 'department' in row and row['department'].strip():
                        department_name = row['department'].strip()
                        department, created = Department.objects.get_or_create(name=department_name)
                    
                    # Get or create course
                    course_name = row['course'].strip()
                    course, created = Course.objects.get_or_create(name=course_name)
                    
                    # If department is provided, associate it with the course
                    if department and not course.department:
                        course.department = department
                        course.save()
                    
                    # Get or create year-section
                    year = row['year'].strip()
                    section = row['section'].strip()
                    year_section, created = YearSection.objects.get_or_create(
                        course=course, 
                        year=year, 
                        section=section
                    )
                    
                    # Check if student already exists
                    school_id = row['school_id'].strip()
                    if Student.objects.filter(school_id=school_id).exists():
                        error_count += 1
                        error_messages.append(f"Row {reader.line_num}: Student with ID {school_id} already exists")
                        continue
                    
                    # Create student
                    student = Student.objects.create(
                        first_name=row['first_name'].strip(),
                        last_name=row['last_name'].strip(),
                        middle_name=row.get('middle_name', '').strip(),
                        email=row['email'].strip(),
                        contact_number=row.get('contact_number', '').strip(),
                        school_id=school_id,
                        department=department,
                        course=course,
                        year_section=year_section,
                        semester=row.get('semester', '1st_semester').strip(),
                        term=row.get('term', 'midterm').strip()
                    )
                    
                    # The QR code is generated automatically in the save method
                    # No need to call generate_qr_code() explicitly
                    
                    success_count += 1
            except Exception as e:
                error_count += 1
                error_messages.append(f"Row {reader.line_num}: {str(e)}")
        
        # Show results
        if success_count > 0:
            messages.success(request, f'Successfully imported {success_count} students')
        
        if error_count > 0:
            messages.error(request, f'Failed to import {error_count} students')
            for error in error_messages[:5]:  # Show first 5 errors
                messages.error(request, error)
            if error_count > 5:
                messages.error(request, f'... and {error_count - 5} more errors')
    
    return redirect('dashboard:admin_students')

@login_required
def import_courses(request):
    """
    Import courses and sections from CSV file
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']
        
        # Check if file is CSV
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file')
            return redirect('dashboard:admin_courses')
        
        # Try to decode the file
        try:
            csv_data = csv_file.read().decode('utf-8')
        except UnicodeDecodeError:
            messages.error(request, 'Please upload a valid CSV file with UTF-8 encoding')
            return redirect('dashboard:admin_courses')
        
        # Process CSV data
        csv_io = io.StringIO(csv_data)
        reader = csv.DictReader(csv_io)
        
        # Get required fields
        required_fields = ['course', 'year', 'section']
        for field in required_fields:
            if field not in reader.fieldnames:
                messages.error(request, f'Missing required field: {field}')
                return redirect('dashboard:admin_courses')
        
        # Process rows
        success_count = 0
        error_count = 0
        error_messages = []
        
        for row in reader:
            try:
                with transaction.atomic():
                    # Get or create course
                    course_name = row['course'].strip()
                    course, created = Course.objects.get_or_create(name=course_name)
                    
                    # Create year-section
                    year = row['year'].strip()
                    section = row['section'].strip()
                    year_section, created = YearSection.objects.get_or_create(
                        course=course, 
                        year=year, 
                        section=section
                    )
                    
                    success_count += 1
            except Exception as e:
                error_count += 1
                error_messages.append(f"Row {reader.line_num}: {str(e)}")
        
        # Show results
        if success_count > 0:
            messages.success(request, f'Successfully imported {success_count} course sections')
        
        if error_count > 0:
            messages.error(request, f'Failed to import {error_count} course sections')
            for error in error_messages[:5]:  # Show first 5 errors
                messages.error(request, error)
            if error_count > 5:
                messages.error(request, f'... and {error_count - 5} more errors')
    
    return redirect('dashboard:admin_courses')

@login_required
def print_qr(request, student_id=None):
    """
    Print QR code for a single student
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Check if student_id is in GET parameters (from modal form)
    if student_id == 0 and request.GET.get('student_id'):
        student_id = request.GET.get('student_id')
    
    student = get_object_or_404(Student, id=student_id)
    
    context = {
        'student': student,
        'print_mode': True,
    }
    
    return render(request, 'dashboard/admin/print_qr.html', context)

@login_required
def print_qr_batch(request):
    """
    Print QR codes for multiple students
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        student_ids = request.POST.getlist('student_ids[]')
        
        if not student_ids:
            messages.error(request, 'Please select at least one student')
            return redirect('dashboard:admin_students')
        
        students = Student.objects.filter(id__in=student_ids)
        
        context = {
            'students': students,
            'print_mode': True,
        }
        
        return render(request, 'dashboard/admin/print_qr_batch.html', context)
    
    return redirect('dashboard:admin_students')

@login_required
def export_data(request, data_type):
    """
    Export data to Excel/CSV format
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Create temporary file
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as temp_file:
        temp_filename = temp_file.name
    
    # Create Excel workbook
    workbook = xlsxwriter.Workbook(temp_filename)
    worksheet = workbook.add_worksheet(data_type.capitalize())
    
    # Add headers and styles
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#f2f2f2',
        'border': 1
    })
    
    date_format = workbook.add_format({'num_format': 'yyyy-mm-dd'})
    
    # Export different data types
    if data_type == 'students':
        # Define headers
        headers = ['ID', 'School ID', 'First Name', 'Middle Name', 'Last Name', 'Email', 
                  'Contact Number', 'Course', 'Year', 'Section', 'Semester', 'Clearance Status', 
                  'Created At']
        
        # Write headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        
        # Get data
        students = Student.objects.all().order_by('last_name', 'first_name')
        
        # Write data
        for row, student in enumerate(students, start=1):
            worksheet.write(row, 0, student.id)
            worksheet.write(row, 1, student.school_id)
            worksheet.write(row, 2, student.first_name)
            worksheet.write(row, 3, student.middle_name or '')
            worksheet.write(row, 4, student.last_name)
            worksheet.write(row, 5, student.email)
            worksheet.write(row, 6, student.contact_number or '')
            worksheet.write(row, 7, student.course.name)
            worksheet.write(row, 8, student.year_section.year)
            worksheet.write(row, 9, student.year_section.section)
            worksheet.write(row, 10, student.get_semester_display())
            worksheet.write(row, 11, student.get_clearance_status_display())
            worksheet.write(row, 12, student.created_at.strftime('%Y-%m-%d'), date_format)
    
    elif data_type == 'clearance':
        # Define headers
        headers = ['ID', 'Student', 'School ID', 'Course', 'Year & Section', 
                  'Status', 'Created By', 'Created At', 'Fields Count', 'Signed Fields']
        
        # Write headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        
        # Get data
        clearance_forms = ClearanceForm.objects.all().order_by('-created_at')
        
        # Write data
        for row, form in enumerate(clearance_forms, start=1):
            total_fields = form.fields.count()
            signed_fields = form.fields.filter(status=True).count()
            
            worksheet.write(row, 0, form.id)
            worksheet.write(row, 1, f"{form.student.first_name} {form.student.last_name}")
            worksheet.write(row, 2, form.student.school_id)
            worksheet.write(row, 3, form.student.course.name)
            worksheet.write(row, 4, f"{form.student.year_section.year} - {form.student.year_section.section}")
            worksheet.write(row, 5, form.get_status_display())
            worksheet.write(row, 6, form.created_by.username if form.created_by else 'N/A')
            worksheet.write(row, 7, form.created_at.strftime('%Y-%m-%d'), date_format)
            worksheet.write(row, 8, total_fields)
            worksheet.write(row, 9, signed_fields)
    
    elif data_type == 'reports':
        # Define headers
        headers = ['ID', 'Staff', 'Date', 'Submitted', 'Student Count', 'Fields Signed']
        
        # Write headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        
        # Get data
        reports = DailyReport.objects.all().order_by('-date')
        
        # Write data
        for row, report in enumerate(reports, start=1):
            student_count = len(report.entries)
            fields_count = sum(len(entry.get('fields', [])) for entry in report.entries.values())
            
            worksheet.write(row, 0, report.id)
            worksheet.write(row, 1, report.staff.username)
            worksheet.write(row, 2, report.date.strftime('%Y-%m-%d'), date_format)
            worksheet.write(row, 3, 'Yes' if report.submitted else 'No')
            worksheet.write(row, 4, student_count)
            worksheet.write(row, 5, fields_count)
    
    elif data_type == 'courses':
        # Define headers
        headers = ['ID', 'Name', 'Students Count', 'Sections Count']
        
        # Write headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        
        # Get data with annotations
        courses = Course.objects.annotate(
            students_count=Count('students'),
            sections_count=Count('year_sections')
        ).order_by('name')
        
        # Write data
        for row, course in enumerate(courses, start=1):
            worksheet.write(row, 0, course.id)
            worksheet.write(row, 1, course.name)
            worksheet.write(row, 2, course.students_count)
            worksheet.write(row, 3, course.sections_count)
    
    # Close workbook
    workbook.close()
    
    try:
        # Send file
        with open(temp_filename, 'rb') as f:
            file_data = f.read()
        
        response = HttpResponse(file_data)
        response['Content-Disposition'] = f'attachment; filename="{data_type}_{datetime.now().strftime("%Y%m%d")}.xlsx"'
        response['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        # Clean up (only after reading the file)
        os.unlink(temp_filename)
        
        return response
    except Exception as e:
        # In case of error, make sure we clean up
        try:
            os.unlink(temp_filename)
        except:
            pass
        raise e

@login_required
def export_pdf(request):
    """
    Export reports data to PDF format using ReportLab (more compatible with Windows)
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Parse filter parameters
    staff_id = request.GET.get('staff_id', '')
    date_filter = request.GET.get('date', '')
    course_id = request.GET.get('course_id', '')
    submitted = request.GET.get('submitted', '')
    
    # Build query
    query = Q()
    
    if staff_id:
        query &= Q(staff_id=staff_id)
    
    if date_filter:
        try:
            date_obj = datetime.strptime(date_filter, '%Y-%m-%d').date()
            query &= Q(date=date_obj)
        except ValueError:
            pass
    
    # Get all reports based on filter
    reports = DailyReport.objects.filter(query).order_by('-date')
    
    # Process course filtering at Python level
    if course_id:
        filtered_reports = []
        for report in reports:
            for entry in report.entries.values():
                student_id = entry.get('student_id')
                if student_id:
                    try:
                        student = Student.objects.get(id=student_id)
                        if student.course and str(student.course.id) == str(course_id):
                            filtered_reports.append(report)
                            break
                    except Student.DoesNotExist:
                        pass
        reports = filtered_reports
    
    # Process submitted filtering
    if submitted:
        submitted_bool = submitted.lower() == 'true'
        reports = [r for r in reports if r.submitted == submitted_bool]
    
    # Prepare data for PDF
    report_data = []
    for report in reports:
        student_count = len(report.entries)
        fields_count = sum(len(entry.get('fields', [])) for entry in report.entries.values())
        
        report_data.append({
            'id': report.id,
            'staff': report.staff.get_full_name() or report.staff.username,
            'date': report.date.strftime('%b %d, %Y'),
            'submitted': 'Yes' if report.submitted else 'No',
            'student_count': student_count,
            'fields_count': fields_count
        })
    
    # Create temporary file for PDF
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
        temp_filename = temp_file.name
    
    try:
        # Try to generate PDF using ReportLab
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            
            # Create PDF document
            doc = SimpleDocTemplate(temp_filename, pagesize=letter)
            elements = []
            
            # Add styles
            styles = getSampleStyleSheet()
            title_style = styles['Heading1']
            subtitle_style = styles['Heading2']
            normal_style = styles['Normal']
            
            # Add title
            elements.append(Paragraph(f"Clearance Reports", title_style))
            elements.append(Spacer(1, 12))
            
            # Add metadata
            elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
            elements.append(Paragraph(f"Total Reports: {len(report_data)}", normal_style))
            elements.append(Spacer(1, 12))
            
            # Create table data
            data = [['Staff', 'Date', 'Students', 'Status', 'Fields Signed']]
            
            # Add report data
            for report in report_data:
                data.append([
                    report['staff'],
                    report['date'],
                    str(report['student_count']),
                    report['submitted'],
                    str(report['fields_count'])
                ])
            
            # Create table
            table = Table(data, repeatRows=1)
            
            # Add table style
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            
            # Add table to elements
            elements.append(table)
            
            # Build PDF
            doc.build(elements)
            
            # Return PDF file
            with open(temp_filename, 'rb') as f:
                file_data = f.read()
            
            response = HttpResponse(file_data, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="reports_{datetime.now().strftime("%Y%m%d")}.pdf"'
            
            # Clean up
            os.unlink(temp_filename)
            
            return response
            
        except ImportError:
            # Fallback to plain HTML if ReportLab is not available
            html_content = f"""
            <html>
            <head>
                <title>Reports - {datetime.now().strftime("%Y-%m-%d")}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; }}
                    h2 {{ color: #333; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                    th {{ background-color: #f2f2f2; }}
                </style>
            </head>
            <body>
                <h2>Clearance Reports</h2>
                <p><strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                <p><strong>Total Reports:</strong> {len(report_data)}</p>
                
                <table>
                    <thead>
                        <tr>
                            <th>Staff</th>
                            <th>Date</th>
                            <th>Students</th>
                            <th>Status</th>
                            <th>Fields Signed</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for report in report_data:
                html_content += f"""
                        <tr>
                            <td>{report['staff']}</td>
                            <td>{report['date']}</td>
                            <td>{report['student_count']}</td>
                            <td>{report['submitted']}</td>
                            <td>{report['fields_count']}</td>
                        </tr>
                """
            
            html_content += """
                    </tbody>
                </table>
            </body>
            </html>
            """
            
            with open(temp_filename, 'wb') as f:
                f.write(html_content.encode('utf-8'))
            
            with open(temp_filename, 'rb') as f:
                file_data = f.read()
            
            response = HttpResponse(file_data, content_type='text/html')
            response['Content-Disposition'] = f'attachment; filename="reports_{datetime.now().strftime("%Y%m%d")}.html"'
            
            # Clean up
            os.unlink(temp_filename)
            
            return response
            
    except Exception as e:
        # In case of error, make sure we clean up
        try:
            os.unlink(temp_filename)
        except:
            pass
        
        # Return the error message
        return HttpResponse(f"Error generating PDF: {str(e)}", content_type='text/plain', status=500)

@login_required
def admin_scan_qr(request):
    """
    Admin QR code scanning view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Process scan
    if request.method == 'POST':
        token = request.POST.get('token')
        
        if token:
            try:
                # Get student by token
                student = Student.objects.get(token=token)
                
                # Check if student has a clearance form
                try:
                    clearance_form = ClearanceForm.objects.get(student=student)
                    
                    # Redirect to clearance detail view
                    return redirect('clearance:clearance_detail', token=student.token)
                    
                except ClearanceForm.DoesNotExist:
                    messages.warning(request, f'Student {student.first_name} {student.last_name} does not have a clearance form yet.')
            except Student.DoesNotExist:
                messages.error(request, 'Invalid QR code. Student not found.')
        else:
            messages.error(request, 'Invalid request. Please scan a valid QR code.')
    
    return render(request, 'dashboard/admin/scan_qr.html')

@login_required
def admin_templates(request):
    """
    Admin clearance templates management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Handle form submissions
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'save_template':
            # Save template to database
            template_name = request.POST.get('template_name')
            field_names = request.POST.getlist('field_name[]')
            staff_ids = request.POST.getlist('staff_assign[]')
            department_id = request.POST.get('department_id')
            year_level_target = request.POST.get('year_level_target', 'all')
            
            # Get department if provided
            department = None
            if department_id:
                try:
                    department = Department.objects.get(id=department_id)
                except Department.DoesNotExist:
                    pass
            
            if template_name and field_names:
                # Create fields data for template
                fields_data = []
                
                # Create template fields
                for i, field_name in enumerate(field_names):
                    if field_name.strip():
                        staff_id = None
                        if i < len(staff_ids) and staff_ids[i]:
                            staff_id = staff_ids[i]
                        
                        fields_data.append({
                            'name': field_name.strip(),
                            'staff_id': staff_id
                        })
                
                # Check if we're updating an existing template
                template_id = request.POST.get('template_id')
                if template_id:
                    try:
                        # Update existing template
                        template = ClearanceTemplate.objects.get(id=template_id)
                        template.name = template_name
                        template.fields = fields_data
                        template.department = department
                        template.year_level_target = year_level_target
                        template.save()
                        messages.success(request, f'Template "{template_name}" updated successfully.')
                    except ClearanceTemplate.DoesNotExist:
                        # Create new template
                        template = ClearanceTemplate.objects.create(
                            name=template_name,
                            fields=fields_data,
                            department=department,
                            year_level_target=year_level_target,
                            created_by=request.user
                        )
                        messages.success(request, f'Template "{template_name}" created successfully.')
                else:
                    # Create new template
                    template = ClearanceTemplate.objects.create(
                        name=template_name,
                        fields=fields_data,
                        department=department,
                        year_level_target=year_level_target,
                        created_by=request.user
                    )
                    messages.success(request, f'Template "{template_name}" created successfully.')
                
                # Store active template in session for form creation
                request.session['active_template_id'] = str(template.id)
            else:
                messages.error(request, 'Please provide a template name and at least one field.')
        
        elif action == 'activate_template':
            # Activate template and apply to students without clearance forms
            template_id = request.POST.get('template_id')
            
            if template_id:
                try:
                    template = ClearanceTemplate.objects.get(id=template_id)
                    
                    # Set as active
                    template.is_active = True
                    template.save()
                    
                    # Apply template to students without clearance forms
                    # Filter students by department and year level if template has those filters
                    student_query = Student.objects.exclude(id__in=ClearanceForm.objects.values_list('student_id', flat=True))
                    
                    if template.department:
                        student_query = student_query.filter(department=template.department)
                    
                    if template.year_level_target != 'all':
                        if template.year_level_target == '1st_to_3rd':
                            # Filter for 1st to 3rd year students - match exact spacing from database
                            student_query = student_query.filter(
                                Q(year_section__year='1st Year') |
                                Q(year_section__year='2nd Year') |
                                Q(year_section__year='3rd Year')
                            )
                        elif template.year_level_target == '4th_year':
                            # Filter for 4th year students - match exact spacing from database
                            student_query = student_query.filter(year_section__year='4th Year')
                    
                    created_count = 0
                    
                    for student in student_query:
                        # Create clearance form
                        clearance_form = ClearanceForm.objects.create(
                            student=student,
                            created_by=request.user
                        )
                        
                        # Create clearance fields
                        for field_data in template.fields:
                            # Check if this is a landlord field
                            if field_data['name'].lower().find('landlord') != -1 or field_data['name'].lower().find('landlady') != -1:
                                # For landlord fields, we'll create a special field that can be signed by any landlord
                                # Get or create a special placeholder user for landlord fields
                                # For landlord fields, create a separate field for EACH landlord user
                                try:
                                    # Get all landlord users
                                    landlord_users = get_all_landlord_users()
                                    
                                    if landlord_users.exists():
                                        # Create a field for each landlord
                                        for landlord_user in landlord_users:
                                            ClearanceField.objects.create(
                                                form=clearance_form,
                                                name=field_data['name'],
                                                assigned_to=landlord_user,  # Assign to each landlord
                                                status=False
                                            )
                                        print(f"DEBUG: Created landlord field for {landlord_users.count()} landlords")
                                    else:
                                        # If no landlords exist, use the admin as placeholder
                                        ClearanceField.objects.create(
                                            form=clearance_form,
                                            name=field_data['name'],
                                            assigned_to=active_template.created_by,  # Use template creator instead of request.user
                                            status=False
                                        )
                                        print(f"DEBUG: No landlords found, assigned to admin")
                                except Exception as e:
                                    print(f"ERROR creating landlord field: {str(e)}")
                                    # Fallback to admin user
                                    ClearanceField.objects.create(
                                        form=clearance_form,
                                        name=field_data['name'],
                                        assigned_to=active_template.created_by,  # Use template creator instead of request.user
                                        status=False
                                    )
                            else:
                                # For regular fields, use the assigned staff or current user
                                staff = None
                                if field_data.get('staff_id'):
                                    try:
                                        staff = User.objects.get(id=field_data['staff_id'])
                                    except User.DoesNotExist:
                                        # If assigned staff doesn't exist, use the current user as a fallback
                                        staff = request.user
                                else:
                                    # If no staff is assigned, use the current user as a fallback
                                    staff = request.user
                                
                                ClearanceField.objects.create(
                                    form=clearance_form,
                                    name=field_data['name'],
                                    assigned_to=staff,
                                    status=False
                                )
                            created_count += 1
                    
                    messages.success(request, f'Template "{template.name}" activated and applied to {created_count} students.')
                except ClearanceTemplate.DoesNotExist:
                    messages.error(request, 'Template not found.')
            else:
                messages.error(request, 'Invalid template ID.')
        
        elif action == 'delete_template':
            # Delete template
            template_id = request.POST.get('template_id')
            
            if template_id:
                try:
                    template = ClearanceTemplate.objects.get(id=template_id)
                    template_name = template.name
                    template.delete()
                    messages.success(request, f'Template "{template_name}" deleted successfully.')
                except ClearanceTemplate.DoesNotExist:
                    messages.error(request, 'Template not found.')
            else:
                messages.error(request, 'Invalid template ID.')
        
        elif action == 'load_template':
            # Load template for editing
            template_id = request.POST.get('template_id')
            
            if template_id:
                try:
                    template = ClearanceTemplate.objects.get(id=template_id)
                    request.session['active_template_id'] = str(template.id)
                    messages.success(request, f'Template "{template.name}" loaded successfully.')
                except ClearanceTemplate.DoesNotExist:
                    messages.error(request, 'Template not found.')
            else:
                messages.error(request, 'Invalid template ID.')
        
        return redirect('dashboard:admin_templates')
    
    # Get department filter from query parameters
    department_filter = request.GET.get('department')
    
    # Get all saved templates
    templates_query = ClearanceTemplate.objects.all()
    
    # Apply department filter if provided
    if department_filter:
        try:
            department_id = int(department_filter)
            templates_query = templates_query.filter(department_id=department_id)
        except (ValueError, TypeError):
            pass
    
    templates = templates_query
    
    # Get current active template
    active_template = None
    active_template_id = request.session.get('active_template_id')
    
    if active_template_id:
        try:
            active_template = ClearanceTemplate.objects.get(id=active_template_id)
        except ClearanceTemplate.DoesNotExist:
            # If the active template doesn't exist, try to get the active one
            active_template = ClearanceTemplate.objects.filter(is_active=True).first()
            if active_template:
                request.session['active_template_id'] = str(active_template.id)
    else:
        # If no active template in session, try to get the active one
        active_template = ClearanceTemplate.objects.filter(is_active=True).first()
        if active_template:
            request.session['active_template_id'] = str(active_template.id)
    
    # Get all staff users for reference (exclude landlords)
    staff_users = User.objects.filter(
        Q(profile__user_type='staff') | Q(profile__user_type='admin') | Q(profile__user_type='user')
    ).filter(
        ~Q(profile__is_landlord=True)  # Exclude landlord users
    ).order_by('last_name')
    
    # Get all landlord users
    landlord_users = User.objects.filter(
        profile__is_landlord=True
    ).order_by('last_name')
    
    # Get all departments for dropdown
    departments = Department.objects.all().order_by('name')
    
    context = {
        'templates': templates,
        'active_template': active_template,
        'staff_users': staff_users,
        'landlord_users': landlord_users,
        'departments': departments,
    }
    
    return render(request, 'dashboard/admin/templates.html', context)

@login_required
def admin_scan_to_sign(request):
    """
    Admin view for scanning QR codes to sign approval fields
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Check if admin has a signature
    if not request.user.profile.signature:
        messages.warning(request, 'You need to set up your signature before you can sign approval fields.')
        return redirect('dashboard:staff_profile')
    
    student = None
    clearance_form = None
    approval_dean = None
    approval_registrar = None
    
    # Process scan or manual token
    token = request.GET.get('token') or request.POST.get('token')
    
    if token:
        try:
            # Get student by token
            student = Student.objects.get(token=token)
            
            # Check if student has a clearance form
            try:
                clearance_form = ClearanceForm.objects.get(student=student)
                
                # Get all fields
                all_fields = clearance_form.fields.all()
                
                # Find landlord/landlady fields
                landlord_fields = all_fields.filter(
                    Q(name__icontains='landlord') | Q(name__icontains='landlady')
                )
                
                # Get non-landlord fields
                non_landlord_fields = all_fields.exclude(
                    Q(name__icontains='landlord') | Q(name__icontains='landlady')
                )
                
                # Calculate correct total fields (count landlord fields as one)
                total_fields = non_landlord_fields.count()
                if landlord_fields.exists():
                    total_fields += 1  # Only count landlord fields as one field
                
                # Calculate signed fields (for landlord fields, count as signed if any one is signed)
                signed_fields = non_landlord_fields.filter(status=True).count()
                if landlord_fields.filter(status=True).exists():
                    signed_fields += 1  # Count as signed if any landlord field is signed
                
                clearance_form.total_fields = total_fields
                clearance_form.signed_fields = signed_fields
                
                if total_fields > 0:
                    clearance_form.progress_percentage = int((signed_fields / total_fields) * 100)
                else:
                    clearance_form.progress_percentage = 0
                
                # Get approval signatures
                try:
                    approval_dean = ApprovalSignature.objects.get(form=clearance_form, role='dean')
                except ApprovalSignature.DoesNotExist:
                    approval_dean = None
                
                try:
                    approval_registrar = ApprovalSignature.objects.get(form=clearance_form, role='registrar')
                except ApprovalSignature.DoesNotExist:
                    approval_registrar = None
                
            except ClearanceForm.DoesNotExist:
                messages.warning(request, f'Student {student.first_name} {student.last_name} does not have a clearance form yet.')
                clearance_form = None
                
        except Student.DoesNotExist:
            messages.error(request, 'Invalid QR code. Student not found.')
    
    context = {
        'student': student,
        'clearance_form': clearance_form,
        'approval_dean': approval_dean,
        'approval_registrar': approval_registrar,
    }
    
    return render(request, 'dashboard/admin/scan_to_sign.html', context)

@login_required
def sign_approval(request):
    """
    Sign approval fields for a clearance form
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        form_id = request.POST.get('form_id')
        role = request.POST.get('role')
        
        if form_id and role:
            try:
                # Get clearance form
                clearance_form = ClearanceForm.objects.get(id=form_id)
                
                # Check if approval already exists
                if ApprovalSignature.objects.filter(form=clearance_form, role=role).exists():
                    messages.warning(request, f'This form already has a {role} approval signature.')
                else:
                    # Ensure staff has a signature
                    if not request.user.profile.signature:
                        messages.error(request, 'You need to set up your signature before you can sign approval fields.')
                        return redirect('dashboard:staff_profile')
                    
                    # Create approval signature
                    approval = ApprovalSignature.objects.create(
                        form=clearance_form,
                        role=role,
                        signed_by=request.user,
                        signature=request.user.profile.signature,
                        signed_by_name=request.user.get_full_name() or request.user.username
                    )
                    
                    role_display = "Dean" if role == "dean" else "College Registrar"
                    messages.success(request, f'Successfully signed as {role_display} for {clearance_form.student.first_name} {clearance_form.student.last_name}')
                
                # Redirect back to the scan page with the student token
                redirect_url = f"{reverse('dashboard:admin_scan_to_sign')}?token={clearance_form.student.token}"
                return redirect(redirect_url)
                
            except ClearanceForm.DoesNotExist:
                messages.error(request, 'Clearance form not found.')
        else:
            messages.error(request, 'Invalid request. Missing form ID or role.')
    
    return redirect('dashboard:admin_scan_to_sign')

@login_required
def get_report_details(request):
    """
    API endpoint to get daily report details by ID
    """
    report_id = request.GET.get('id')
    
    if not report_id:
        return JsonResponse({'success': False, 'error': 'Report ID is required'})
    
    try:
        report = DailyReport.objects.get(id=report_id)
        
        # Format the entries data
        entries_data = []
        for student_id, entry in report.entries.items():
            entries_data.append({
                'id': entry.get('school_id', 'N/A'),
                'name': entry.get('student_name', 'Unknown'),
                'fields': entry.get('fields', []),
                'time': entry.get('time', 'N/A')
            })
            
        data = {
            'success': True,
            'report': {
                'id': report.id,
                'staff': report.staff.get_full_name() or report.staff.username,
                'date': report.date.strftime('%b %d, %Y'),
                'status': 'Submitted' if report.submitted else 'Not Submitted',
                'submitted_at': report.updated_at.strftime('%b %d, %Y %I:%M %p') if report.submitted else 'N/A',
                'entries': entries_data
            }
        }
        
        return JsonResponse(data)
    
    except DailyReport.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Report not found'})

@login_required
def admin_departments(request):
    """
    Admin departments management view
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        if action == 'add':
            # Add new department
            name = request.POST.get('name', '').strip()
            
            if name:
                # Check if department with same name exists
                if Department.objects.filter(name=name).exists():
                    messages.error(request, f'Department "{name}" already exists')
                else:
                    department = Department.objects.create(name=name)
                    messages.success(request, f'Department "{name}" added successfully')
            else:
                messages.error(request, 'Please enter a department name')
        
        elif action == 'edit':
            # Edit department
            department_id = request.POST.get('department_id')
            name = request.POST.get('name', '').strip()
            
            if department_id and name:
                try:
                    department = Department.objects.get(id=department_id)
                    
                    # Check if another department with same name exists
                    if Department.objects.filter(name=name).exclude(id=department_id).exists():
                        messages.error(request, f'Department "{name}" already exists')
                    else:
                        department.name = name
                        department.save()
                        messages.success(request, f'Department "{name}" updated successfully')
                except Department.DoesNotExist:
                    messages.error(request, 'Department not found')
            else:
                messages.error(request, 'Please enter a department name')
        
        elif action == 'delete':
            # Delete department
            department_id = request.POST.get('department_id')
            
            if department_id:
                try:
                    department = Department.objects.get(id=department_id)
                    name = department.name
                    
                    # Check if department has courses
                    if department.courses.exists():
                        messages.error(request, f'Cannot delete department "{name}" because it has courses assigned to it')
                    else:
                        department.delete()
                        messages.success(request, f'Department "{name}" deleted successfully')
                except Department.DoesNotExist:
                    messages.error(request, 'Department not found')
            else:
                messages.error(request, 'Invalid department ID')
    
    # Get all departments
    departments = Department.objects.all().order_by('name')
    
    context = {
        'departments': departments,
    }
    
    return render(request, 'dashboard/admin/departments.html', context)

@receiver(post_save, sender=Student)
def create_clearance_form_for_new_student(sender, instance, created, **kwargs):
    """
    Create a clearance form for a new student using the active template
    """
    if created:
        # Check if student already has a clearance form
        if hasattr(instance, 'clearance_form'):
            return
        
        # Make sure department is assigned from course if not already set
        if not instance.department and instance.course and instance.course.department:
            instance.department = instance.course.department
            # Save without triggering this signal again
            Student.objects.filter(id=instance.id).update(department=instance.course.department)
            # Refresh the instance to get the updated department
            instance.refresh_from_db()
        
        # Find appropriate template based on department and year level
        template_query = ClearanceTemplate.objects.filter(is_active=True)
        
        # Filter by department if student has one
        department_template_exists = False
        if instance.department:
            dept_templates = template_query.filter(department=instance.department)
            if dept_templates.exists():
                template_query = dept_templates
                department_template_exists = True
            else:
                # No template exists for this department, don't create a clearance form
                print(f"DEBUG: No active clearance template found for department {instance.department}. Skipping form creation.")
                return
        else:
            # No department assigned and no department-agnostic template, don't create a form
            if not template_query.filter(department__isnull=True).exists():
                print(f"DEBUG: Student has no department and no department-agnostic template exists. Skipping form creation.")
                return
        
        # Filter by year level
        year = instance.year_section.year if instance.year_section else None
        year_template_exists = False
        
        if year:
            if year in ['1st Year', '2nd Year', '3rd Year']:
                year_templates = template_query.filter(year_level_target__in=['all', '1st_to_3rd'])
                if year_templates.exists():
                    template_query = year_templates
                    year_template_exists = True
                else:
                    print(f"DEBUG: No active clearance template found for year level {year}. Skipping form creation.")
                    return
            elif year == '4th Year':
                year_templates = template_query.filter(year_level_target__in=['all', '4th_year'])
                if year_templates.exists():
                    template_query = year_templates
                    year_template_exists = True
                else:
                    print(f"DEBUG: No active clearance template found for 4th Year. Skipping form creation.")
                    return
        
        # Get the most specific template (prioritize department and year level specific templates)
        active_template = template_query.first()
        
        if not active_template:
            print(f"DEBUG: No suitable clearance template found for student {instance.school_id}. Skipping form creation.")
            return
        
        # Create clearance form
        clearance_form = ClearanceForm.objects.create(
            student=instance,
            created_by=active_template.created_by
        )
        
        # Create clearance fields
        for field_data in active_template.fields:
            # Check if this is a landlord field
            if field_data['name'].lower().find('landlord') != -1 or field_data['name'].lower().find('landlady') != -1:
                # For landlord fields, create a separate field for EACH landlord user
                try:
                    # Get all landlord users
                    landlord_users = get_all_landlord_users()
                    
                    if landlord_users.exists():
                        # Create a field for each landlord
                        for landlord_user in landlord_users:
                            ClearanceField.objects.create(
                                form=clearance_form,
                                name=field_data['name'],
                                assigned_to=landlord_user,  # Assign to each landlord
                                status=False
                            )
                            print(f"DEBUG: assign {landlord_user} clearance forms")
                        print(f"DEBUG: Created landlord field for {landlord_users.count()} landlords")
                    else:
                        # If no landlords exist, use the admin as placeholder
                        ClearanceField.objects.create(
                            form=clearance_form,
                            name=field_data['name'],
                            assigned_to=active_template.created_by,  # Use template creator instead of request.user
                            status=False
                        )
                        print(f"DEBUG: No landlords found, assigned to admin")
                except Exception as e:
                    print(f"ERROR creating landlord field: {str(e)}")
                    # Fallback to admin user
                    ClearanceField.objects.create(
                        form=clearance_form,
                        name=field_data['name'],
                        assigned_to=active_template.created_by,  # Use template creator instead of request.user
                        status=False
                    )
            else:
                # For regular fields, use the assigned staff or current user
                staff = None
                if field_data.get('staff_id'):
                    try:
                        staff = User.objects.get(id=field_data['staff_id'])
                    except User.DoesNotExist:
                        # If assigned staff doesn't exist, use the template creator as a fallback
                        staff = active_template.created_by
                else:
                    # If no staff is assigned, use the template creator as a fallback
                    staff = active_template.created_by
                
                ClearanceField.objects.create(
                    form=clearance_form,
                    name=field_data['name'],
                    assigned_to=staff,
                    status=False
                )

@login_required
def admin_landlords(request):
    """
    Admin view to display all landlord users and their signature history
    """
    if not request.user.is_superuser and not (hasattr(request.user, 'profile') and request.user.profile.user_type == 'admin'):
        return redirect('dashboard:staff_dashboard')
    
    # Get all landlord users
    landlords = User.objects.filter(profile__is_landlord=True).order_by('last_name', 'first_name')
    
    # Get signature counts for each landlord
    for landlord in landlords:
        landlord.signature_count = LandlordSignature.objects.filter(landlord=landlord).count()
    
    # Get landlord ID from request if viewing a specific landlord's signatures
    landlord_id = request.GET.get('landlord_id')
    selected_landlord = None
    landlord_signatures = []
    
    if landlord_id:
        try:
            selected_landlord = User.objects.get(id=landlord_id, profile__is_landlord=True)
            landlord_signatures = LandlordSignature.objects.filter(
                landlord=selected_landlord
            ).select_related('student').order_by('-created_at')
        except User.DoesNotExist:
            messages.error(request, 'Landlord not found.')
    
    context = {
        'landlords': landlords,
        'selected_landlord': selected_landlord,
        'landlord_signatures': landlord_signatures,
    }
    
    return render(request, 'dashboard/admin/landlords.html', context)

def update_student_departments():
    """
    Update student departments based on their course departments
    """
    updated_count = 0
    
    # Get all students without department but with courses that have departments
    students = Student.objects.filter(
        department__isnull=True,
        course__department__isnull=False
    )
    
    for student in students:
        student.department = student.course.department
        student.save(update_fields=['department'])
        updated_count += 1
    
    if updated_count > 0:
        print(f"DEBUG: Updated departments for {updated_count} students based on their courses")

@receiver(post_save, sender=Course)
def update_students_when_course_department_changes(sender, instance, **kwargs):
    """
    Update student departments when a course's department is updated
    """
    if instance.department:
        # Get students in this course without a department
        students = Student.objects.filter(course=instance, department__isnull=True)
        updated_count = students.count()
        
        # Update departments
        if updated_count > 0:
            students.update(department=instance.department)
            print(f"Updated departments for {updated_count} students based on course {instance.name}")
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseRedirect
import paypalrestsdk

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt  # optional
from django.conf import settings
import paypalrestsdk

@login_required
def process_payment(request):
    """
    Handle PayPal payment creation for the selected plan and billing cycle.
    """
    if request.method == "POST":
        plan = request.POST.get("plan")
        billing_cycle = request.POST.get("billing_cycle", "monthly").lower()

        # Define valid pricing
        plan_prices = {
            "basic": {"monthly": "15.00", "yearly": "150.00"},
            "pro": {"monthly": "25.00", "yearly": "250.00"},
            "enterprise": {"monthly": "30.00", "yearly": "300.00"},
        }

        # Validate
        if plan not in plan_prices or billing_cycle not in plan_prices[plan]:
            return JsonResponse({"error": "Invalid plan or billing cycle"}, status=400)

        amount = plan_prices[plan][billing_cycle]
        label = f"{plan.capitalize()} Plan ({billing_cycle.capitalize()})"

        # Create PayPal payment
        payment = paypalrestsdk.Payment({
            "intent": "sale",
            "payer": {"payment_method": "paypal"},
            "redirect_urls": {
                "return_url": request.build_absolute_uri("/dashboard/payment-success/"),
                "cancel_url": request.build_absolute_uri("/dashboard/payment-cancelled/")
            },
            "transactions": [{
                "item_list": {
                    "items": [{
                        "name": label,
                        "sku": f"{plan}-{billing_cycle}",
                        "price": amount,
                        "currency": "USD",
                        "quantity": 1
                    }]
                },
                "amount": {
                    "total": amount,
                    "currency": "USD"
                },
                "description": f"Subscription payment for {label}"
            }]
        })

        if payment.create():
            for link in payment.links:
                if link.method == "REDIRECT":
                    return HttpResponseRedirect(link.href)
            return JsonResponse({"error": "No redirect URL found."}, status=500)
        else:
            return JsonResponse({"error": "Payment creation failed", "details": payment.error}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)
from django.shortcuts import render
from django.contrib import messages
from django.contrib.auth.decorators import login_required

@login_required
def subscription_view(request):
    return render(request, 'dashboard/subscription.html')

@login_required
def payment_success(request):
    """
    Handle successful payment callback from PayPal
    """
    messages.success(request, "Your payment was successful! Thank you for subscribing.")
    return render(request, "dashboard/payment_success.html")

@login_required
def payment_cancelled(request):
    """
    Handle cancelled payment
    """
    messages.warning(request, "Your payment was cancelled.")
    return render(request, "dashboard/payment_cancelled.html")

