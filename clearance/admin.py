from django.contrib import admin
from .models import Student, ClearanceTemplate, ClearanceForm, ClearanceField, ApprovalSignature, DailyReport

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('school_id', 'last_name', 'first_name', 'course', 'year_section', 'clearance_status')
    list_filter = ('course', 'year_section', 'clearance_status', 'semester')
    search_fields = ('school_id', 'last_name', 'first_name', 'email')
    readonly_fields = ('token', 'qr_code')

@admin.register(ClearanceTemplate)
class ClearanceTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_by', 'created_at')
    list_filter = ('is_active', 'created_by')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    actions = ['activate_template']
    
    def activate_template(self, request, queryset):
        for template in queryset:
            template.is_active = True
            template.save()
        self.message_user(request, f"{queryset.count()} template(s) activated successfully.")
    
    activate_template.short_description = "Activate selected templates"

class ClearanceFieldInline(admin.TabularInline):
    model = ClearanceField
    extra = 0

class ApprovalSignatureInline(admin.TabularInline):
    model = ApprovalSignature
    extra = 0
    max_num = 2

@admin.register(ClearanceForm)
class ClearanceFormAdmin(admin.ModelAdmin):
    list_display = ('student', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'created_by')
    search_fields = ('student__school_id', 'student__last_name')
    inlines = [ClearanceFieldInline, ApprovalSignatureInline]

@admin.register(ClearanceField)
class ClearanceFieldAdmin(admin.ModelAdmin):
    list_display = ('name', 'form', 'assigned_to', 'status', 'signed_at')
    list_filter = ('status', 'assigned_to')
    search_fields = ('name', 'form__student__school_id')
    readonly_fields = ('signed_at',)

@admin.register(ApprovalSignature)
class ApprovalSignatureAdmin(admin.ModelAdmin):
    list_display = ('role', 'form', 'signed_by', 'signed_at')
    list_filter = ('role', 'signed_by')
    search_fields = ('form__student__school_id', 'signed_by__username')
    readonly_fields = ('signed_at',)

@admin.register(DailyReport)
class DailyReportAdmin(admin.ModelAdmin):
    list_display = ('staff', 'date', 'submitted', 'created_at')
    list_filter = ('staff', 'date', 'submitted')
    search_fields = ('staff__username',)
    readonly_fields = ('date', 'entries')
