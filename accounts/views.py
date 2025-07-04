from django.shortcuts import render as django_render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import LoginForm, UserProfileForm
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse
from django.template.loader import get_template, render_to_string
from xhtml2pdf import pisa
from django.utils import timezone
from django.conf import settings
from .models import UserProfile
from datetime import timedelta
import os
import base64
import uuid


def login_view(request):
    if request.user.is_authenticated:
        return _redirect_user_by_type(request.user)

    form = LoginForm()

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password']
            )
            if user:
                login(request, user)
                return _redirect_user_by_type(user)
            else:
                messages.error(request, 'Invalid login credentials.')

    return django_render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
def profile_view(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=request.user.profile)
        if form.is_valid():
            signature_data = form.cleaned_data.get('signature_data')
            if signature_data and 'data:image/png;base64,' in signature_data:
                image_data = base64.b64decode(signature_data.split(',')[1])
                filename = f"signature_{request.user.username}_{uuid.uuid4().hex[:8]}.png"
                request.user.profile.signature.save(filename, ContentFile(image_data), save=False)
            form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('accounts:profile')
    else:
        form = UserProfileForm(instance=request.user.profile)
    return django_render(request, 'accounts/profile.html', {'form': form})


@login_required
def toggle_auto_sign(request):
    if request.method == 'POST':
        if request.user.profile.user_type in ['staff', 'user']:
            request.user.profile.auto_sign = not request.user.profile.auto_sign
            request.user.profile.save()
            status = "enabled" if request.user.profile.auto_sign else "disabled"
            messages.success(request, f'Auto-sign mode has been {status}.')
    return redirect('accounts:profile')


def _redirect_user_by_type(user):
    if hasattr(user, 'profile'):
        if user.profile.user_type == 'admin' or user.is_superuser:
            return redirect('dashboard:admin_dashboard')
        elif user.profile.user_type == 'user':
            return redirect('dashboard:user_dashboard')
        else:
            return redirect('dashboard:staff_dashboard')
    return redirect('dashboard:user_dashboard')


@login_required
def subscription_view(request):
    profile = request.user.profile
    now = timezone.now()

    if profile.trial_start_date and not profile.has_active_subscription:
        trial_end = profile.trial_start_date + timezone.timedelta(days=14)
        if now > trial_end:
            profile.has_active_subscription = False
            profile.save()
            messages.warning(request, "Your 14-day trial has ended. Please subscribe to continue.")
            return django_render(request, 'accounts/subscription.html')

    if profile.subscription_end_date:
        days_left = (profile.subscription_end_date - now).days
        if 0 < days_left <= 5:
            messages.info(request, f"Your subscription will expire in {days_left} day(s).")

        if now > profile.subscription_end_date:
            profile.has_active_subscription = False
            profile.save()
            messages.warning(request, "Your subscription has ended. Please renew to continue.")
            return django_render(request, 'accounts/subscription.html')

    return redirect('dashboard:user_dashboard')


@login_required
def generate_invoice(request):
    profile = request.user.profile
    template_path = 'accounts/invoice.html'

    context = {
        'user': request.user,
        'profile': profile,
        'amount_paid': request.session.get('amount_paid', 'N/A'),
        'transaction_id': request.session.get('transaction_id', 'N/A'),
        'payment_date': request.session.get('payment_date', 'N/A'),
        'plan': request.session.get('plan', 'N/A'),
    }

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="invoice.pdf"'

    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('We had some errors with generating the invoice.')
    return response


@login_required
def download_invoice(request):
    transaction_id = request.session.get('paypal_transaction_id')
    amount_paid = request.session.get('paypal_amount')
    plan = request.session.get('paypal_plan')

    if not transaction_id or not amount_paid:
        messages.error(request, "Invoice information not found.")
        return redirect('dashboard:subscription')

    context = {
        'user': request.user,
        'transaction_id': transaction_id,
        'amount_paid': amount_paid,
        'plan': plan,
        'payment_date': timezone.now(),
    }

    html = render_to_string('accounts/invoice.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=invoice_{transaction_id}.pdf'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('We had some errors while generating your invoice.')
    return response


@login_required
def activate_subscription(request, plan):
    user_profile = request.user.profile
    now = timezone.now()

    user_profile.subscription_start_date = now
    if plan == 'monthly':
        user_profile.subscription_end_date = now + timedelta(days=30)
    elif plan == 'yearly':
        user_profile.subscription_end_date = now + timedelta(days=365)

    user_profile.has_active_subscription = True
    user_profile.save()

    messages.success(request, f"Your {plan} subscription has been activated.")
    return redirect('dashboard:user_dashboard')
from django.views.decorators.csrf import csrf_protect

from django.shortcuts import render, redirect
from django.core.mail import send_mail
from django.contrib import messages
from django.views.decorators.csrf import csrf_protect

@csrf_protect
def request_demo_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        institution = request.POST.get('institution')
        phone = request.POST.get('phone')
        message = request.POST.get('message')

        subject = f'New Demo Request from {name}'
        full_message = f"""
        Demo Request Details:

        Name: {name}
        Email: {email}
        Institution: {institution}
        Phone: {phone}

        Message:
        {message}
        """

        try:
            send_mail(
                subject,
                full_message,
                'danmugo42@gmail.com',     # Sender
                ['danmugo42@gmail.com'],   # Recipient
                fail_silently=False,
            )
            messages.success(request, 'Your request was sent successfully! We will reach out soon.')
            return redirect('/')  # or your thank-you page
        except Exception as e:
            messages.error(request, f'Failed to send demo request: {e}')

    return render(request, 'accounts/request_demo.html')
from django.http import HttpResponse
from django.contrib.auth import get_user_model

from django.http import HttpResponse
from django.contrib.auth import get_user_model

def create_my_superuser(request):
    User = get_user_model()
    if not User.objects.filter(username='Danmugo').exists():
        user = User.objects.create_superuser(
            username='Danmugo',
            email='danmugo42@gmail.com',
            password='Mugo@ClearTrack2025'
        )
        user.is_staff = True
        user.is_superuser = True
        user.save()
        return HttpResponse("✅ Superuser created. You can now log in at /admin/")
    return HttpResponse("ℹ️ Superuser already exists.")

from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings

def request_access(request):
    if request.method == 'POST':
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')
        username = request.POST.get('username')
        password = request.POST.get('password')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name
            )
            user.is_active = False  # Admin must activate them later
            user.save()

            # ✅ Send notification email to admin
            try:
                send_mail(
                    subject='[ClearTrack] New Access Request',
                    message=f'''
A new user has requested access to ClearTrack.

Name: {first_name} {last_name}
Email: {email}
Username: {username}

Please log in to the admin panel to review and activate this user.
''',
                    from_email=settings.EMAIL_HOST_USER,
                    recipient_list=[settings.EMAIL_HOST_USER],
                    fail_silently=False
                )
                messages.success(request, 'Request submitted! Admin will activate your account.')
            except Exception as e:
                messages.error(request, f'Request created, but email failed: {str(e)}')

        return redirect(request.META.get('HTTP_REFERER', '/'))
    return redirect('/')
