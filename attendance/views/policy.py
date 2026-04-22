"""
policy.py

Views for AttendancePolicy CRUD and AttendancePolicyViolation management.
"""

import json
from urllib.parse import parse_qs

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from attendance.filters import AttendancePolicyFilter, AttendancePolicyViolationFilter
from attendance.forms import AttendancePolicyForm
from attendance.methods.utils import paginator_qry
from attendance.models import (
    AttendancePolicy,
    AttendancePolicyViolation,
)
from base.methods import closest_numbers, filtersubordinates, is_reportingmanager, sortby
from horilla.group_by import group_by_queryset


# ── Policy CRUD ──────────────────────────────────────────────────────────────


@login_required
def attendance_policy_view(request):
    """Main list page for attendance policies."""
    policies = AttendancePolicy.objects.all()
    if not request.user.has_perm("attendance.view_attendancepolicy"):
        policies = AttendancePolicy.objects.none()
    return render(
        request,
        "attendance/policy/policies.html",
        {
            "policies": policies,
        },
    )


@login_required
def attendance_policy_search(request):
    """HTMX search / filter / paginate for attendance policies."""
    previous_data = request.GET.urlencode()
    policies = AttendancePolicy.objects.all()
    if not request.user.has_perm("attendance.view_attendancepolicy"):
        policies = AttendancePolicy.objects.none()
    filter_obj = AttendancePolicyFilter(request.GET, queryset=policies)
    field = request.GET.get("field", "")
    policies_qs = filter_obj.qs
    template = "attendance/policy/policy_list.html"
    if field and field is not None:
        policies_qs = group_by_queryset(
            policies_qs, field, request.GET.get("page"), "page"
        )
        template = "attendance/policy/group_by.html"
    else:
        policies_qs = paginator_qry(policies_qs, request.GET.get("page"))
    return render(
        request,
        template,
        {
            "policies": policies_qs,
            "pd": previous_data,
            "filter_dict": dict(parse_qs(previous_data)),
            "field": field,
            "f": filter_obj,
        },
    )


@login_required
def attendance_policy_create(request):
    """Create a new attendance policy."""
    if not request.user.has_perm("attendance.add_attendancepolicy"):
        messages.error(request, _("You do not have permission to create policies."))
        return HttpResponse("<script>window.location.reload();</script>")
    form = AttendancePolicyForm()
    if request.method == "POST":
        form = AttendancePolicyForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance policy created successfully."))
            return HttpResponse("<script>window.location.reload();</script>")
    return render(
        request,
        "attendance/policy/policy_form.html",
        {"form": form},
    )


@login_required
def attendance_policy_update(request, obj_id):
    """Update an existing attendance policy."""
    if not request.user.has_perm("attendance.change_attendancepolicy"):
        messages.error(request, _("You do not have permission to edit policies."))
        return HttpResponse("<script>window.location.reload();</script>")
    policy = AttendancePolicy.objects.filter(id=obj_id).first()
    if not policy:
        messages.error(request, _("Policy not found."))
        return HttpResponse("<script>window.location.reload();</script>")
    form = AttendancePolicyForm(instance=policy)
    if request.method == "POST":
        form = AttendancePolicyForm(request.POST, instance=policy)
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance policy updated successfully."))
            return HttpResponse("<script>window.location.reload();</script>")
    return render(
        request,
        "attendance/policy/policy_form.html",
        {"form": form},
    )


@login_required
@require_http_methods(["POST"])
def attendance_policy_delete(request, obj_id):
    """Delete an attendance policy."""
    if not request.user.has_perm("attendance.delete_attendancepolicy"):
        messages.error(request, _("You do not have permission to delete policies."))
        return HttpResponse("<script>window.location.reload();</script>")
    try:
        policy = AttendancePolicy.objects.get(id=obj_id)
        policy.delete()
        messages.success(request, _("Attendance policy deleted successfully."))
    except AttendancePolicy.DoesNotExist:
        messages.error(request, _("Policy not found."))
    return HttpResponse("<script>window.location.reload();</script>")


# ── Policy Violation views ───────────────────────────────────────────────────


@login_required
def policy_violation_view(request):
    """Main list page for attendance policy violations."""
    violations = AttendancePolicyViolation.objects.all()
    violations = filtersubordinates(
        request, violations, "attendance.view_attendancepolicyviolation"
    )
    return render(
        request,
        "attendance/policy/violations.html",
        {
            "violations": violations,
        },
    )


@login_required
def policy_violation_search(request):
    """HTMX search / filter / paginate for policy violations."""
    previous_data = request.GET.urlencode()
    violations = AttendancePolicyViolation.objects.all()
    violations = filtersubordinates(
        request, violations, "attendance.view_attendancepolicyviolation"
    )
    filter_obj = AttendancePolicyViolationFilter(request.GET, queryset=violations)
    field = request.GET.get("field", "")
    violations_qs = sortby(request, filter_obj.qs, "week_start_date")
    violation_ids = json.dumps(
        [
            instance.id
            for instance in paginator_qry(
                violations_qs, request.GET.get("page")
            ).object_list
        ]
    )
    template = "attendance/policy/violation_list.html"
    if field and field is not None:
        violations_qs = group_by_queryset(
            violations_qs, field, request.GET.get("page"), "page"
        )
        template = "attendance/policy/group_by.html"
    else:
        violations_qs = paginator_qry(violations_qs, request.GET.get("page"))
    return render(
        request,
        template,
        {
            "violations": violations_qs,
            "violation_ids": violation_ids,
            "pd": previous_data,
            "filter_dict": dict(parse_qs(previous_data)),
            "field": field,
        },
    )


@login_required
def policy_violation_single_view(request, obj_id):
    """Single-record detail modal for a policy violation."""
    violation = AttendancePolicyViolation.objects.filter(id=obj_id).first()
    previous_id, next_id = closest_numbers(
        list(AttendancePolicyViolation.objects.values_list("id", flat=True)),
        obj_id,
    )
    instance_ids_json = request.GET.get("instances_ids", "[]")
    return render(
        request,
        "attendance/policy/single_violation.html",
        {
            "violation": violation,
            "pd": request.GET.urlencode(),
            "previous_instance": previous_id,
            "next_instance": next_id,
            "instance_ids_json": instance_ids_json,
        },
    )


@login_required
@require_http_methods(["POST"])
def policy_violation_resolve(request, obj_id):
    """Mark a policy violation as resolved."""
    if not (
        request.user.has_perm("attendance.resolve_attendancepolicyviolation")
        or hasattr(request.user, "employee_get")
    ):
        messages.error(request, _("You do not have permission to resolve violations."))
        return HttpResponse("<script>window.location.reload();</script>")

    violation = AttendancePolicyViolation.objects.filter(id=obj_id).first()
    if violation:
        violation.is_resolved = True
        violation.resolved_by = request.user.employee_get
        violation.note = request.POST.get("note", "")
        violation.save()
        messages.success(request, _("Violation marked as resolved."))
    return HttpResponse(
        '<span hx-get="{}" hx-target="#policyViolationContainer" hx-trigger="load"></span>'.format(
            reverse("policy-violation-search") + "?" + request.GET.urlencode()
        )
    )


@login_required
@require_http_methods(["POST"])
def policy_violation_delete(request, obj_id):
    """Delete a single policy violation record."""
    try:
        violation = AttendancePolicyViolation.objects.get(id=obj_id)
        violation.delete()
        messages.success(request, _("Violation deleted successfully."))
    except AttendancePolicyViolation.DoesNotExist:
        messages.error(request, _("Violation not found."))
    return HttpResponse(
        '<span hx-get="{}" hx-target="#policyViolationContainer" hx-trigger="load"></span>'.format(
            reverse("policy-violation-search") + "?" + request.GET.urlencode()
        )
    )


@login_required
@require_http_methods(["POST"])
def policy_violation_bulk_delete(request):
    """Bulk delete policy violation records."""
    ids = request.POST.getlist("ids")
    AttendancePolicyViolation.objects.filter(id__in=ids).delete()
    messages.success(request, _("Selected violations deleted."))
    return HttpResponse(
        '<span hx-get="{}" hx-target="#policyViolationContainer" hx-trigger="load"></span>'.format(
            reverse("policy-violation-search") + "?" + request.GET.urlencode()
        )
    )
