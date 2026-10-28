from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PrincipalSerializer


class MeView(APIView):
    """GET /api/auth/me — the authenticated principal + their roles."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(PrincipalSerializer(request.user).data)
