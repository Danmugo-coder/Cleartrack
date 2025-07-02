import datetime
from django.shortcuts import redirect
from django.utils.timezone import now
from django.contrib import messages
from accounts.models import UserProfile

class SubscriptionMiddleware:
    """
    Enforces 14-day trial and active subscription logic
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            try:
                profile = user.profile
                today = now().date()

                # 14-day trial logic
                if profile.trial_start_date:
                    days_since_trial = (today - profile.trial_start_date.date()).days
                    if days_since_trial > 14 and not profile.plan:
                        messages.warning(request, "Your 14-day trial has expired. Please subscribe to continue.")
                        return redirect('/dashboard/subscription/')

                # Subscription expiration check
                if profile.plan and profile.subscription_end_date:
                    sub_end_date = profile.subscription_end_date.date()
                    if today > sub_end_date:
                        profile.plan = ''  # Reset plan
                        profile.subscription_start_date = None
                        profile.subscription_end_date = None
                        profile.save()
                        messages.error(request, "Your subscription has expired. Please renew to continue.")
                        return redirect('/dashboard/subscription/')

                    # Reminder logic (e.g., 5 days before expiration)
                    days_remaining = (sub_end_date - today).days
                    if 0 < days_remaining <= 5:
                        messages.info(request, f"Your subscription expires in {days_remaining} day(s). Please renew soon.")

            except UserProfile.DoesNotExist:
                pass

        return self.get_response(request)
