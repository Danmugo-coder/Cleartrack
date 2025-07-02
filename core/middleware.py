import datetime
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse

class TrialAndSubscriptionMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated or request.user.is_staff:
            return None

        if request.path.startswith(reverse("dashboard:subscription")):
            return None

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return None

        today = datetime.date.today()

        # ✅ Trial Period Logic
        if profile.trial_start_date:
            trial_end = profile.trial_start_date + datetime.timedelta(days=14)
            if today > trial_end and not profile.has_active_subscription:
                return redirect("/dashboard/subscription/")

        # ✅ Subscription Expiry Logic
        if profile.subscription_end_date:
            if today > profile.subscription_end_date:
                return redirect("/dashboard/subscription/")
            elif profile.subscription_end_date - today <= datetime.timedelta(days=5):
                request.subscription_warning = True

        return None
