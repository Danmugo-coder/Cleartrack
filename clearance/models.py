from django.db import models
from django.contrib.auth.models import User
from core.models import Course, YearSection, Department
from django.db.models.signals import post_save
from django.dispatch import receiver
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image, ImageDraw
from django.conf import settings
import os
import uuid
import json

class Student(models.Model):
    SEMESTER_CHOICES = (
        ('1st_semester', '1st Semester'),
        ('2nd_semester', '2nd Semester'),
        ('summer', 'Summer'),
    )
    
    TERM_CHOICES = (
        ('midterm', 'Midterm'),
        ('final', 'Final'),
    )
    
    STATUS_CHOICES = (
        ('in_progress', 'In Progress'),
        ('complete', 'Complete'),
    )
    
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    contact_number = models.CharField(max_length=20)
    school_id = models.CharField(max_length=50, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='students', null=True, blank=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='students')
    year_section = models.ForeignKey(YearSection, on_delete=models.CASCADE, related_name='students')
    semester = models.CharField(max_length=15, choices=SEMESTER_CHOICES, default='1st_semester')
    term = models.CharField(max_length=10, choices=TERM_CHOICES, default='midterm')
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    clearance_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.last_name}, {self.first_name} - {self.school_id}"
    
    def save(self, *args, **kwargs):
        # Generate QR code if it doesn't exist
        if not self.qr_code:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr_data = f"{settings.SITE_URL}/clearance/{self.token}/" if hasattr(settings, 'SITE_URL') else f"http://localhost:8000/clearance/{self.token}/"#Change the url in production
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Save QR code
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = f"qr_{self.school_id}.png"
            self.qr_code.save(filename, File(buffer), save=False)
            
        super().save(*args, **kwargs)
    
    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Student'
        verbose_name_plural = 'Students'

class ClearanceTemplate(models.Model):
    YEAR_LEVEL_CHOICES = (
        ('all', 'All Years'),
        ('1st_to_3rd', '1st to 3rd Year'),
        ('4th_year', '4th Year'),
    )
    
    name = models.CharField(max_length=100)
    fields = models.JSONField(default=list)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='templates', null=True, blank=True)
    year_level_target = models.CharField(max_length=20, choices=YEAR_LEVEL_CHOICES, default='all')
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        dept_name = self.department.name if self.department else "All Departments"
        return f"{self.name} - {dept_name} - {self.get_year_level_target_display()} {'(Active)' if self.is_active else ''}"
    
    def save(self, *args, **kwargs):
        # If this template is being set as active, deactivate all others with the same department and year level target
        if self.is_active:
            ClearanceTemplate.objects.filter(
                department=self.department,
                year_level_target=self.year_level_target
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
    
    class Meta:
        ordering = ['-is_active', '-created_at']
        verbose_name = 'Clearance Template'
        verbose_name_plural = 'Clearance Templates'

class ClearanceForm(models.Model):
    STATUS_CHOICES = (
        ('incomplete', 'Incomplete'),
        ('complete', 'Complete'),
    )
    
    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name='clearance_form')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_forms')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='incomplete')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Clearance Form - {self.student.school_id}"
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Clearance Form'
        verbose_name_plural = 'Clearance Forms'

class ClearanceField(models.Model):
    form = models.ForeignKey(ClearanceForm, on_delete=models.CASCADE, related_name='fields')
    name = models.CharField(max_length=100)
    assigned_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assigned_fields')
    signature = models.ImageField(upload_to='signatures/', null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    status = models.BooleanField(default=False)
    signed_by_name = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name} - {self.form.student.school_id}"
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Clearance Field'
        verbose_name_plural = 'Clearance Fields'

class DailyReport(models.Model):
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_reports')
    date = models.DateField(auto_now_add=True)
    entries = models.JSONField(default=dict)
    submitted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Report - {self.staff.username} - {self.date}"
    
    class Meta:
        ordering = ['-date']
        verbose_name = 'Daily Report'
        verbose_name_plural = 'Daily Reports'
        unique_together = ['staff', 'date']

class ApprovalSignature(models.Model):
    ROLE_CHOICES = (
        ('dean', 'Dean'),
        ('registrar', 'College Registrar'),
    )
    
    form = models.ForeignKey(ClearanceForm, on_delete=models.CASCADE, related_name='approvals')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    signed_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='approvals')
    signature = models.ImageField(upload_to='signatures/', null=True, blank=True)
    signed_by_name = models.CharField(max_length=255, blank=True, null=True)
    signed_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.get_role_display()} Approval for {self.form.student.school_id}"
    
    class Meta:
        unique_together = ['form', 'role']
        ordering = ['role']
        verbose_name = 'Approval Signature'
        verbose_name_plural = 'Approval Signatures'

class LandlordSignature(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='landlord_signatures')
    landlord = models.ForeignKey(User, on_delete=models.CASCADE, related_name='landlord_signatures')
    signature = models.ImageField(upload_to='signatures/', null=True, blank=True)
    signed_by_name = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Landlord Signature - {self.landlord.get_full_name()} for {self.student.school_id}"
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Landlord Signature'
        verbose_name_plural = 'Landlord Signatures'
        unique_together = ['student']
