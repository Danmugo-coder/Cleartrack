from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import Department
from datetime import timedelta
from django.utils import timezone

class UserProfile(models.Model):
    USER_TYPE_CHOICES = (
        ('admin', 'Admin'),
        ('staff', 'Staff'),
        ('user', 'User'),
    )

    PLAN_CHOICES = (
        ('free_trial', 'Free Trial'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    user_type = models.CharField(max_length=10, choices=USER_TYPE_CHOICES, default='staff')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    is_landlord = models.BooleanField(default=False)
    signature = models.ImageField(upload_to='signatures/', null=True, blank=True)
    auto_sign = models.BooleanField(default=False)

    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free_trial')
    trial_start_date = models.DateTimeField(auto_now_add=True)
    subscription_start_date = models.DateTimeField(null=True, blank=True)
    subscription_end_date = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_trial_expired(self):
        return timezone.now() > self.trial_start_date + timedelta(days=14)

    def is_subscription_expired(self):
        return self.subscription_end_date and timezone.now() > self.subscription_end_date

    def days_until_expiry(self):
        if self.subscription_end_date:
            return (self.subscription_end_date - timezone.now()).days
        return 0

    def __str__(self):
        return f"{self.user.username} - {self.get_user_type_display()}"

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
