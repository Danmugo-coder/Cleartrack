from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from .models import Student, ClearanceForm, ClearanceField, ApprovalSignature, DailyReport
from django.db.models import Q
import json

@login_required
def clearance_detail(request, token):
    """
    View for displaying a student's clearance form
    """
    student = get_object_or_404(Student, token=token)
    
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
            total_fields += 1
        
        # Calculate signed fields (for landlord fields, count as signed if any one is signed)
        signed_fields = non_landlord_fields.filter(status=True).count()
        if landlord_fields.filter(status=True).exists():
            signed_fields += 1 
        
        if total_fields > 0:
            progress_percentage = int((signed_fields / total_fields) * 100)
        else:
            progress_percentage = 0
        
        # Add calculated values to clearance_form
        clearance_form.total_fields = total_fields
        clearance_form.signed_fields = signed_fields
        clearance_form.progress_percentage = progress_percentage
        
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
        messages.error(request, "This student does not have a clearance form yet.")
        return redirect('dashboard:admin_dashboard')
    
    # Get fields assigned to the current user
    assigned_fields = clearance_form.fields.filter(assigned_to=request.user)
    
    context = {
        'student': student,
        'clearance_form': clearance_form,
        'assigned_fields': assigned_fields,
        'approval_dean': approval_dean,
        'approval_registrar': approval_registrar,
    }
    
    return render(request, 'clearance/detail.html', context)

@login_required
def sign_clearance(request, field_id):
    """
    Sign a clearance field
    """
    field = get_object_or_404(ClearanceField, id=field_id)
    
    # Check if the field is assigned to the current user
    if field.assigned_to != request.user:
        messages.error(request, 'You are not authorized to sign this field')
        return redirect('dashboard:staff_dashboard')
    
    if request.method == 'POST':
        # Get signature from user profile
        if not request.user.profile.signature:
            messages.error(request, 'Please upload your signature first')
            return redirect('accounts:profile')
        
        # Update field status
        field.signature = request.user.profile.signature
        field.signed_at = timezone.localtime()
        field.status = True
        field.signed_by_name = request.user.get_full_name() or request.user.username
        field.save()
        
        # Add to daily report
        today = timezone.localtime().date()
        
        # Get or create today's report
        try:
            report = DailyReport.objects.get(
                staff=request.user,
                date=today
            )
            
            # If today's report was already submitted, create a new one
            if report.submitted:
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
        
        # Update report entries
        entries = report.entries or {}
        student_id = str(field.form.student.id)
        
        if student_id not in entries:
            entries[student_id] = {
                'student_id': str(field.form.student.id),
                'student_name': f"{field.form.student.first_name} {field.form.student.last_name}",
                'school_id': field.form.student.school_id,
                'course': field.form.student.course.name if field.form.student.course else '',
                'fields': [field.name],
                'time': timezone.localtime().strftime('%H:%M')
            }
        else:
            if field.name not in entries[student_id]['fields']:
                entries[student_id]['fields'].append(field.name)
        
        report.entries = entries
        report.save()
        
        # Check if all fields are signed
        all_signed = True
        for f in field.form.fields.all():
            if not f.status:
                all_signed = False
                break
        
        # Update form status if all fields are signed
        if all_signed:
            field.form.status = 'complete'
            field.form.save()
            
            # Update student clearance status
            student = field.form.student
            student.clearance_status = 'complete'
            student.save()
        
        messages.success(request, 'Clearance field signed successfully')
        return redirect('clearance:clearance_detail', token=field.form.student.token)
    
    context = {
        'field': field,
    }
    
    return render(request, 'clearance/sign.html', context)
