from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PrincipalSerializer, RegisterSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register — create a principal (open for the demo)."""
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(APIView):
    """GET /api/auth/me — the authenticated principal + their roles."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(PrincipalSerializer(request.user).data)
