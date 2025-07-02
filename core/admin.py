from django.contrib import admin
from .models import Department, Course, YearSection, SystemBackup

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'created_at')
    search_fields = ('name',)
    list_filter = ('department',)
    prepopulated_fields = {'slug': ('name',)}

@admin.register(YearSection)
class YearSectionAdmin(admin.ModelAdmin):
    list_display = ('course', 'year', 'section')
    search_fields = ('year', 'section', 'course__name')
    list_filter = ('course',)

@admin.register(SystemBackup)
class SystemBackupAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'backup_file', 'size_bytes', 'created_at', 'is_successful')
    list_filter = ('is_successful', 'created_at')
    readonly_fields = ('created_at',)
