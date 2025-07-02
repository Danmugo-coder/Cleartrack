from django.db import models
from django.utils.text import slugify

# Create your models here.

class Department(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

class Course(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Course'
        verbose_name_plural = 'Courses'

class YearSection(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='year_sections')
    year = models.CharField(max_length=20)
    section = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.course.name} - {self.year} - {self.section}"
    
    class Meta:
        ordering = ['course', 'year', 'section']
        verbose_name = 'Year & Section'
        verbose_name_plural = 'Year & Sections'
        unique_together = ['course', 'year', 'section']

class SystemBackup(models.Model):
    """
    Model to track system backups
    """
    backup_file = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    is_successful = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"Backup {self.created_at.strftime('%Y-%m-%d %H:%M')}"
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'System Backup'
        verbose_name_plural = 'System Backups'
        get_latest_by = 'created_at'
