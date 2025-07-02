from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from clearance.models import Student, ClearanceForm, ApprovalSignature
import uuid

def home(request):
    """
    Show homepage to guests. Redirect authenticated users to their dashboard.
    """
    if request.user.is_authenticated and hasattr(request.user, 'profile'):
        profile = request.user.profile
        if profile.user_type == 'admin' or request.user.is_superuser:
            return redirect('dashboard:admin_dashboard')
        elif profile.user_type == 'user':
            return redirect('dashboard:user_dashboard')
        else:
            return redirect('dashboard:staff_dashboard')
    return render(request, 'core/home.html')


def scan_clearance(request):
    """
    Allow token-based clearance view access.
    """
    if request.method == 'POST':
        token = request.POST.get('token')
        if token:
            try:
                token_uuid = uuid.UUID(token)
                student = get_object_or_404(Student, token=token_uuid)
                return redirect('core:view_clearance', token=token_uuid)
            except (ValueError, Student.DoesNotExist):
                messages.error(request, 'Invalid token or student not found.')
    return redirect('core:home')


def view_clearance(request, token):
    """
    View a student's clearance form via token.
    """
    try:
        token_uuid = uuid.UUID(str(token))
        student = get_object_or_404(Student, token=token_uuid)
    except (ValueError, Student.DoesNotExist, TypeError):
        messages.error(request, "Invalid student token or student not found.")
        return redirect('core:home')

    try:
        clearance_form = ClearanceForm.objects.get(student=student)
    except ClearanceForm.DoesNotExist:
        messages.error(request, "This student does not have a clearance form yet.")
        return redirect('core:home')

    all_fields = clearance_form.fields.all()

    landlord_fields = all_fields.filter(
        Q(name__icontains='landlord') | Q(name__icontains='landlady')
    )
    non_landlord_fields = all_fields.exclude(
        Q(name__icontains='landlord') | Q(name__icontains='landlady')
    )

    total_fields = non_landlord_fields.count()
    if landlord_fields.exists():
        total_fields += 1

    signed_fields = non_landlord_fields.filter(status=True).count()
    if landlord_fields.filter(status=True).exists():
        signed_fields += 1

    progress_percentage = int((signed_fields / total_fields) * 100) if total_fields > 0 else 0

    clearance_form.total_fields = total_fields
    clearance_form.signed_fields = signed_fields
    clearance_form.progress_percentage = progress_percentage

    context = {
        'student': student,
        'clearance_form': clearance_form,
        'approval_dean': ApprovalSignature.objects.filter(form=clearance_form, role='dean').first(),
        'approval_registrar': ApprovalSignature.objects.filter(form=clearance_form, role='registrar').first(),
    }

    return render(request, 'core/view_clearance.html', context)
