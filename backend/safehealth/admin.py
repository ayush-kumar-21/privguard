from django.contrib import admin

from .models import (
    Department, Diagnosis, Hospital, LabResult, Patient, Region, Staff, Visit,
)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "region", "sex", "birth_year", "zip3", "insurance_type")
    list_filter = ("sex", "insurance_type", "region")
    search_fields = ("mrn", "zip3")


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("visit_id", "patient", "dept", "admission_type", "admit_ts", "total_charge")
    list_filter = ("admission_type", "dept")
    date_hierarchy = "admit_ts"


admin.site.register([Region, Hospital, Department, Staff, Diagnosis, LabResult])
