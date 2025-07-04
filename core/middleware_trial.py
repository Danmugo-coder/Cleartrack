import datetime
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse
from django.contrib import messages
from django.utils.timezone import now

class TrialAndSubscriptionMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated or request.user.is_staff:
            return None

        if request.path.startswith(reverse("dashboard:subscription")):
            return None

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return None

        today = now().date()  # current date

        # 🎯 Trial Period Logic
        if profile.trial_start_date:
            trial_end = profile.trial_start_date + datetime.timedelta(days=14)
            if today > trial_end and not profile.has_active_subscription:
                messages.warning(
                    request,
                    f"🎉 Your 14-day trial expired on {trial_end.strftime('%b %d, %Y')}. "
                    f"Upgrade now to unlock full access to all ClearTrack features!"
                )
                return redirect("/dashboard/subscription/")
            elif 0 <= (trial_end - today).days <= 3:
                messages.info(
                    request,
                    f"⏳ Heads up! Your free trial ends in {(trial_end - today).days} day(s). "
                    f"Consider subscribing to avoid any interruptions."
                )

        # 💼 Subscription Expiry Logic
        if profile.subscription_end_date:
            if today > profile.subscription_end_date:
                messages.warning(
                    request,
                    f"⚠️ Your subscription expired on {profile.subscription_end_date.strftime('%b %d, %Y')}. "
                    f"Please renew your plan to continue using ClearTrack."
                )
                return redirect("/dashboard/subscription/")
            elif 0 <= (profile.subscription_end_date - today).days <= 5:
                remaining = (profile.subscription_end_date - today).days
                messages.info(
                    request,
                    f"🔔 Your subscription expires in {remaining} day(s) on {profile.subscription_end_date.strftime('%b %d, %Y')}. "
                    f"Renew soon to stay on track!"
                )
                request.subscription_warning = True

        return None
