from django.contrib.auth import get_user_model
from rest_framework import serializers

Principal = get_user_model()


class PrincipalSerializer(serializers.ModelSerializer):
    roles = serializers.SlugRelatedField(
        slug_field="role_name", many=True, read_only=True
    )

    class Meta:
        model = Principal
        fields = ("id", "username", "kind", "is_active", "is_staff", "roles", "date_joined")
        read_only_fields = ("id", "is_staff", "date_joined")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Principal
        fields = ("username", "password", "kind")

    def create(self, validated_data):
        # set_password applies the Argon2 hasher configured in settings.
        password = validated_data.pop("password")
        principal = Principal(**validated_data)
        principal.set_password(password)
        principal.save()
        return principal
