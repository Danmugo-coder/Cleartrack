from django.http import HttpResponseForbidden

class RestrictAdminAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only allow you (the developer) by username
        if request.path.startswith('/admin/') and request.user.is_authenticated:
            if request.user.username != 'Danmugo':
                return HttpResponseForbidden("Access Denied: Admin access is restricted.")
        return self.get_response(request)
