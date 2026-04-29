"""
requests.py

This module is used to register the endpoints to the attendance requests
"""

import copy
import datetime
import json
from urllib.parse import parse_qs

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from attendance.filters import AttendanceFilters, AttendanceRequestReGroup
from attendance.forms import (
    AttendanceRequestForm,
    AttendanceShiftRequestForm,
    BatchAttendanceForm,
    BulkAttendanceRequestForm,
    MissedFingerprintRequestForm,
    NewRequestForm,
)
from attendance.methods.utils import (
    activity_datetime,
    format_time,
    get_diff_dict,
    get_employee_last_name,
    overtime_calculation,
    paginator_qry,
    shift_schedule_today,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceLateComeEarlyOut,
    AttendanceShiftRequest,
    BatchAttendance,
    MissedFingerprintRequest,
)
from attendance.views.clock_in_out import (
    _update_attendance_location,
    early_out,
    late_come,
)
from attendance.views.views import attendance_validate
from base.methods import (
    choosesubordinates,
    closest_numbers,
    eval_validate,
    filtersubordinates,
    get_key_instances,
    is_reportingmanager,
)
from base.models import EmployeeShift, EmployeeShiftDay
from employee.models import Employee
from horilla.decorators import (
    hx_request_required,
    login_required,
    manager_can_enter,
    permission_required,
)
from horilla.http import HorillaRedirect
from notifications.signals import notify


@login_required
def request_attendance(request):
    """
    This method is used to render template to register new attendance for a normal user
    """
    if request.GET.get("previous_url"):
        form = AttendanceRequestForm(initial=request.GET.dict())
    else:
        form = AttendanceRequestForm()
    if request.method == "POST":
        form = AttendanceRequestForm(request.POST)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save()
    return render(request, "requests/attendance/form.html", {"form": form})


@login_required
def request_attendance_view(request):
    """
    This method is used to view the attendances for to request
    """
    requests = Attendance.objects.filter(
        is_validate_request=True,
    )
    requests = filtersubordinates(
        request=request,
        perm="attendance.view_attendance",
        queryset=requests,
    )
    requests = requests | Attendance.objects.filter(
        employee_id__employee_user_id=request.user,
        is_validate_request=True,
    )
    requests = AttendanceFilters(request.GET, requests).qs
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(Attendance, data_dict)

    keys_to_remove = [key for key, value in data_dict.items() if value == ["unknown"]]
    for key in keys_to_remove:
        data_dict.pop(key)
    attendances = filtersubordinates(
        request=request,
        perm="attendance.view_attendance",
        queryset=Attendance.objects.all(),
    )
    attendances = attendances | Attendance.objects.filter(
        employee_id__employee_user_id=request.user
    )
    attendances = attendances.filter(
        employee_id__is_active=True,
    )
    attendances = AttendanceFilters(request.GET, attendances).qs
    filter_obj = AttendanceFilters()
    check_attendance = Attendance.objects.all()
    check_shift_requests = AttendanceShiftRequest.objects.all()
    check_missed_fingerprint_requests = MissedFingerprintRequest.objects.all()
    if (
        check_attendance.exists()
        or check_shift_requests.exists()
        or check_missed_fingerprint_requests.exists()
    ):
        template = "requests/attendance/view-requests.html"
    else:
        template = "requests/attendance/requests_empty.html"
    requests_ids = json.dumps(
        [instance.id for instance in paginator_qry(requests, None).object_list]
    )
    attendances_ids = json.dumps(
        [instance.id for instance in paginator_qry(attendances, None).object_list]
    )
    requests = requests.filter(
        employee_id__is_active=True,
    )
    shift_requests = _attendance_shift_requests_queryset(request, request.GET)
    missed_fingerprint_requests = _missed_fingerprint_requests_queryset(
        request, request.GET
    )
    return render(
        request,
        template,
        {
            "requests": paginator_qry(requests, None),
            "attendances": paginator_qry(attendances, None),
            "shift_requests": paginator_qry(shift_requests, None),
            "missed_fingerprint_requests": paginator_qry(
                missed_fingerprint_requests, None
            ),
            "requests_ids": requests_ids,
            "attendances_ids": attendances_ids,
            "f": filter_obj,
            "filter_dict": data_dict,
            "gp_fields": AttendanceRequestReGroup.fields,
        },
    )


def _attendance_shift_requests_queryset(request, params=None):
    params = params or {}
    shift_requests = AttendanceShiftRequest.objects.filter(employee_id__is_active=True)
    shift_requests = filtersubordinates(
        request=request,
        perm="attendance.view_attendance",
        queryset=shift_requests,
    )
    shift_requests = shift_requests | AttendanceShiftRequest.objects.filter(
        employee_id__employee_user_id=request.user,
        employee_id__is_active=True,
    )
    shift_requests = shift_requests.distinct()

    search = params.get("search")
    if search:
        shift_requests = shift_requests.filter(
            Q(employee_id__employee_first_name__icontains=search)
            | Q(employee_id__employee_last_name__icontains=search)
            | Q(description__icontains=search)
        )

    employee_id = params.get("employee_id")
    if employee_id and employee_id not in {"unknown", ""}:
        shift_requests = shift_requests.filter(employee_id_id=employee_id)

    return shift_requests


def _missed_fingerprint_requests_queryset(request, params=None):
    params = params or {}
    fingerprint_requests = MissedFingerprintRequest.objects.filter(
        employee_id__is_active=True
    )
    fingerprint_requests = filtersubordinates(
        request=request,
        perm="attendance.view_attendance",
        queryset=fingerprint_requests,
    )
    fingerprint_requests = fingerprint_requests | MissedFingerprintRequest.objects.filter(
        employee_id__employee_user_id=request.user,
        employee_id__is_active=True,
    )
    fingerprint_requests = fingerprint_requests.distinct()

    search = params.get("search")
    if search:
        fingerprint_requests = fingerprint_requests.filter(
            Q(employee_id__employee_first_name__icontains=search)
            | Q(employee_id__employee_last_name__icontains=search)
            | Q(description__icontains=search)
            | Q(reject_reason__icontains=search)
        )

    employee_id = params.get("employee_id")
    if employee_id and employee_id not in {"unknown", ""}:
        fingerprint_requests = fingerprint_requests.filter(employee_id_id=employee_id)

    return fingerprint_requests


@login_required
@hx_request_required
def request_new(request):
    """
    This method is used to create new attendance requests
    """

    if request.GET.get("bulk") and eval_validate(request.GET.get("bulk")):
        employee = request.user.employee_get
        if request.GET.get("employee_id"):
            form = BulkAttendanceRequestForm(initial=request.GET)
        else:
            form = BulkAttendanceRequestForm(initial={"employee_id": employee})
        if request.method == "POST":
            form = BulkAttendanceRequestForm(request.POST)
            form.instance.attendance_clock_in_date = request.POST.get("from_date")
            form.instance.attendance_date = request.POST.get("from_date")
            if form.is_valid():
                instance = form.save(commit=False)
                messages.success(request, _("Attendance request created"))
                return HttpResponse(
                    render(
                        request,
                        "requests/attendance/request_new_form.html",
                        {"form": form},
                    ).content.decode("utf-8")
                    + "<script>location.reload();</script>"
                )
        return render(
            request,
            "requests/attendance/request_new_form.html",
            {"form": form, "bulk": True},
        )
    if request.GET.get("employee_id"):
        form = NewRequestForm(initial=request.GET.dict())
    else:
        form = NewRequestForm()
    form = choosesubordinates(request, form, "attendance.change_attendance")
    employees_qs = Employee.objects.filter(
        Q(id__in=form.fields["employee_id"].queryset.values_list("id", flat=True))
        | Q(employee_user_id=request.user)
    )

    form.fields["employee_id"].queryset = employees_qs.distinct()
    form.fields["employee_id"].initial = request.user.employee_get.id
    if request.GET.get("emp_id"):
        emp_id = request.GET.get("emp_id")
        form.fields["employee_id"].queryset = Employee.objects.filter(id=emp_id)
        form.fields["employee_id"].initial = emp_id
    if request.method == "POST":
        form = NewRequestForm(request.POST)
        employees_qs = Employee.objects.filter(
            Q(id__in=form.fields["employee_id"].queryset.values_list("id", flat=True))
            | Q(employee_user_id=request.user)
        )
        form.fields["employee_id"].queryset = employees_qs.distinct()
        if form.is_valid():
            if form.new_instance is not None:
                form.new_instance.save()
                messages.success(request, _("New attendance request created"))
                return HttpResponse(
                    render(
                        request,
                        "requests/attendance/request_new_form.html",
                        {"form": form},
                    ).content.decode("utf-8")
                    + "<script>location.reload();</script>"
                )
            messages.success(request, _("Update request updated"))
            return HttpResponse(
                render(
                    request,
                    "requests/attendance/request_new_form.html",
                    {"form": form},
                ).content.decode("utf-8")
                + "<script>location.reload();</script>"
            )
    return render(
        request,
        "requests/attendance/request_new_form.html",
        {"form": form, "bulk": False},
    )


@login_required
@hx_request_required
def attendance_shift_request_create(request):
    """Create a next-week attendance shift request for the current week."""
    form = AttendanceShiftRequestForm()
    if request.method == "POST":
        form = AttendanceShiftRequestForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance shift request created"))
            return HttpResponse(
                render(
                    request,
                    "requests/attendance/shift_request_form.html",
                    {"form": form},
                ).content.decode("utf-8")
                + "<script>location.reload();</script>"
            )
    return render(
        request,
        "requests/attendance/shift_request_form.html",
        {"form": form},
    )


@login_required
def attendance_shift_request_cancel(request, obj_id):
    """Cancel an attendance shift request."""
    shift_request = AttendanceShiftRequest.objects.filter(id=obj_id).first()
    if not shift_request:
        messages.error(request, _("Attendance shift request not found."))
        return HorillaRedirect(request)

    user_employee = getattr(request.user, "employee_get", None)
    is_owner = user_employee and user_employee.id == shift_request.employee_id_id
    can_manage = request.user.has_perm("attendance.change_attendance") or (
        user_employee and user_employee.reporting_manager.all().exists()
    )
    if not (is_owner or can_manage):
        messages.error(request, _("You don't have permission"))
        return HorillaRedirect(request)

    shift_request.canceled = True
    shift_request.save(update_fields=["canceled"])
    messages.success(request, _("Attendance shift request canceled"))
    return HorillaRedirect(request)


@login_required
@hx_request_required
def missed_fingerprint_request_create(request):
    """Create a missed biometric punch request."""
    form = MissedFingerprintRequestForm()
    if request.method == "POST":
        form = MissedFingerprintRequestForm(request.POST)
        if form.is_valid():
            missed_request = form.save()
            messages.success(request, _("Missed fingerprint request created"))
            employee = missed_request.employee_id
            reporting_manager = getattr(
                getattr(employee, "employee_work_info", None),
                "reporting_manager_id",
                None,
            )
            if reporting_manager and reporting_manager.employee_user_id:
                notify.send(
                    request.user,
                    recipient=reporting_manager.employee_user_id,
                    verb=(
                        f"{employee}'s missed fingerprint request for "
                        f"{missed_request.attendance_date} is created"
                    ),
                    verb_ar=(
                        f"تم إنشاء طلب بصمة فائتة لـ {employee} "
                        f"بتاريخ {missed_request.attendance_date}"
                    ),
                    verb_de=(
                        f"Der Antrag auf verpassten Fingerabdruck von {employee} "
                        f"fuer den {missed_request.attendance_date} wurde erstellt"
                    ),
                    verb_es=(
                        f"Se ha creado la solicitud de huella omitida de {employee} "
                        f"para el {missed_request.attendance_date}"
                    ),
                    verb_fr=(
                        f"La demande d'empreinte manquee de {employee} "
                        f"pour le {missed_request.attendance_date} a ete creee"
                    ),
                    redirect=reverse("request-attendance-view"),
                    icon="finger-print-outline",
                )
            return HttpResponse(
                render(
                    request,
                    "requests/attendance/missed_fingerprint_form.html",
                    {"form": form},
                ).content.decode("utf-8")
                + "<script>location.reload();</script>"
            )
    return render(
        request,
        "requests/attendance/missed_fingerprint_form.html",
        {"form": form},
    )


def _can_manage_missed_fingerprint_request(request, missed_request):
    user_employee = getattr(request.user, "employee_get", None)
    if request.user.has_perm("attendance.change_attendance"):
        return True
    return (
        user_employee
        and getattr(missed_request.employee_id, "employee_work_info", None)
        and missed_request.employee_id.employee_work_info.reporting_manager_id
        == user_employee
    )


def _recalculate_attendance_from_activities(attendance):
    activities = AttendanceActivity.objects.filter(
        employee_id=attendance.employee_id,
        attendance_date=attendance.attendance_date,
    ).order_by("clock_in_date", "clock_in", "id")
    first_activity = activities.first()
    closed_activities = activities.filter(clock_out__isnull=False).order_by(
        "clock_out_date", "clock_out", "id"
    )
    last_closed = closed_activities.last()

    if first_activity:
        attendance.attendance_clock_in_date = first_activity.clock_in_date
        attendance.attendance_clock_in = first_activity.clock_in
    if last_closed:
        attendance.attendance_clock_out_date = last_closed.clock_out_date
        attendance.attendance_clock_out = last_closed.clock_out

    duration = 0
    for activity in closed_activities:
        in_datetime, out_datetime = activity_datetime(activity)
        difference = out_datetime - in_datetime
        duration += difference.days * 24 * 3600 + difference.seconds

    attendance.attendance_worked_hour = format_time(duration)
    attendance.attendance_overtime = overtime_calculation(attendance)
    attendance.attendance_validated = attendance_validate(attendance)
    attendance.save()
    _update_attendance_location(attendance)
    return attendance


def _apply_missed_fingerprint_request(missed_request):
    employee = missed_request.employee_id
    work_info = getattr(employee, "employee_work_info", None)
    if not work_info or not work_info.shift_id:
        raise ValidationError(_("Employee work info or shift not found."))

    attendance_date = missed_request.attendance_date
    punch_time = missed_request.punch_time
    punch_datetime = datetime.datetime.combine(attendance_date, punch_time)
    day = EmployeeShiftDay.objects.get(day=attendance_date.strftime("%A").lower())
    shift = work_info.shift_id
    minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
        day=day, shift=shift
    )
    source = (
        "biometric"
        if missed_request.attendance_location
        == MissedFingerprintRequest.AttendanceLocation.OFFICE
        else "app"
    )

    attendance, _created = Attendance.objects.get_or_create(
        employee_id=employee,
        attendance_date=attendance_date,
        defaults={
            "shift_id": shift,
            "work_type_id": work_info.work_type_id,
            "attendance_day": day,
            "minimum_hour": minimum_hour,
            "attendance_location": missed_request.attendance_location,
        },
    )
    attendance.shift_id = attendance.shift_id or shift
    attendance.work_type_id = attendance.work_type_id or work_info.work_type_id
    attendance.attendance_day = attendance.attendance_day or day
    attendance.minimum_hour = attendance.minimum_hour or minimum_hour

    if missed_request.punch_type == MissedFingerprintRequest.PunchType.IN:
        duplicate = AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
            clock_in_date=attendance_date,
            clock_in=punch_time,
        ).exists()
        if duplicate:
            raise ValidationError(_("This check-in punch already exists."))
        activity = AttendanceActivity.objects.create(
            employee_id=employee,
            attendance_date=attendance_date,
            clock_in_date=attendance_date,
            shift_day=day,
            clock_in=punch_time,
            in_datetime=punch_datetime,
            clock_in_source=source,
        )
    else:
        activity = (
            AttendanceActivity.objects.filter(
                employee_id=employee,
                attendance_date=attendance_date,
                clock_out__isnull=True,
            )
            .order_by("clock_in_date", "clock_in", "id")
            .last()
        )
        if not activity:
            raise ValidationError(
                _("A check-out request needs an existing open check-in.")
            )
        in_datetime = activity.in_datetime or datetime.datetime.combine(
            activity.clock_in_date, activity.clock_in
        )
        if punch_datetime <= in_datetime:
            raise ValidationError(_("Check-out time must be after check-in time."))
        activity.clock_out_date = attendance_date
        activity.clock_out = punch_time
        activity.out_datetime = punch_datetime
        activity.clock_out_source = source
        activity.save()

    attendance = _recalculate_attendance_from_activities(attendance)
    if attendance.attendance_clock_in:
        late_come(
            attendance=attendance,
            start_time=start_time_sec,
            end_time=end_time_sec,
            shift=shift,
        )
    if attendance.attendance_clock_out:
        early_out(
            attendance=attendance,
            start_time=start_time_sec,
            end_time=end_time_sec,
            shift=shift,
        )
    return attendance, activity


@login_required
def missed_fingerprint_request_approve(request, obj_id):
    missed_request = MissedFingerprintRequest.objects.filter(id=obj_id).first()
    if not missed_request:
        messages.error(request, _("Missed fingerprint request not found."))
        return HorillaRedirect(request)
    if not _can_manage_missed_fingerprint_request(request, missed_request):
        messages.error(request, _("You don't have permission"))
        return HorillaRedirect(request)
    if missed_request.status != MissedFingerprintRequest.Status.REQUESTED:
        messages.info(
            request, _("Only requested missed fingerprint requests can be approved.")
        )
        return HorillaRedirect(request)

    try:
        attendance, activity = _apply_missed_fingerprint_request(missed_request)
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return HorillaRedirect(request)

    missed_request.status = MissedFingerprintRequest.Status.APPROVED
    missed_request.approved_by = getattr(request.user, "employee_get", None)
    missed_request.reviewed_at = timezone.now()
    missed_request.attendance_id = attendance
    missed_request.activity_id = activity
    missed_request.save(
        update_fields=[
            "status",
            "approved_by",
            "reviewed_at",
            "attendance_id",
            "activity_id",
        ]
    )
    messages.success(request, _("Missed fingerprint request approved"))
    notify.send(
        request.user,
        recipient=missed_request.employee_id.employee_user_id,
        verb=f"Your missed fingerprint request for {missed_request.attendance_date} is approved",
        verb_ar=f"تمت الموافقة على طلب البصمة الفائتة بتاريخ {missed_request.attendance_date}",
        verb_de=f"Ihr Antrag auf verpassten Fingerabdruck fuer den {missed_request.attendance_date} wurde genehmigt",
        verb_es=f"Se aprobo tu solicitud de huella omitida para el {missed_request.attendance_date}",
        verb_fr=f"Votre demande d'empreinte manquee pour le {missed_request.attendance_date} est approuvee",
        redirect=reverse("request-attendance-view"),
        icon="checkmark-circle-outline",
    )
    return HorillaRedirect(request)


@login_required
def missed_fingerprint_request_reject(request, obj_id):
    missed_request = MissedFingerprintRequest.objects.filter(id=obj_id).first()
    if not missed_request:
        messages.error(request, _("Missed fingerprint request not found."))
        return HorillaRedirect(request)
    if not _can_manage_missed_fingerprint_request(request, missed_request):
        messages.error(request, _("You don't have permission"))
        return HorillaRedirect(request)
    if missed_request.status != MissedFingerprintRequest.Status.REQUESTED:
        messages.info(
            request, _("Only requested missed fingerprint requests can be rejected.")
        )
        return HorillaRedirect(request)

    missed_request.status = MissedFingerprintRequest.Status.REJECTED
    missed_request.approved_by = getattr(request.user, "employee_get", None)
    missed_request.reviewed_at = timezone.now()
    missed_request.save(update_fields=["status", "approved_by", "reviewed_at"])
    messages.success(request, _("Missed fingerprint request rejected"))
    notify.send(
        request.user,
        recipient=missed_request.employee_id.employee_user_id,
        verb=f"Your missed fingerprint request for {missed_request.attendance_date} is rejected",
        verb_ar=f"تم رفض طلب البصمة الفائتة بتاريخ {missed_request.attendance_date}",
        verb_de=f"Ihr Antrag auf verpassten Fingerabdruck fuer den {missed_request.attendance_date} wurde abgelehnt",
        verb_es=f"Se rechazo tu solicitud de huella omitida para el {missed_request.attendance_date}",
        verb_fr=f"Votre demande d'empreinte manquee pour le {missed_request.attendance_date} est rejetee",
        redirect=reverse("request-attendance-view"),
        icon="close-circle-outline",
    )
    return HorillaRedirect(request)


@login_required
def missed_fingerprint_request_cancel(request, obj_id):
    missed_request = MissedFingerprintRequest.objects.filter(id=obj_id).first()
    if not missed_request:
        messages.error(request, _("Missed fingerprint request not found."))
        return HorillaRedirect(request)

    user_employee = getattr(request.user, "employee_get", None)
    is_owner = user_employee and user_employee.id == missed_request.employee_id_id
    if not (is_owner or _can_manage_missed_fingerprint_request(request, missed_request)):
        messages.error(request, _("You don't have permission"))
        return HorillaRedirect(request)
    if missed_request.status != MissedFingerprintRequest.Status.REQUESTED:
        messages.info(
            request, _("Only requested missed fingerprint requests can be canceled.")
        )
        return HorillaRedirect(request)

    missed_request.status = MissedFingerprintRequest.Status.CANCELED
    missed_request.save(update_fields=["status"])
    messages.success(request, _("Missed fingerprint request canceled"))
    return HorillaRedirect(request)


@login_required
def create_batch_attendance(request):
    form = BatchAttendanceForm()
    previous_form_data = request.GET.urlencode()
    previous_url = request.GET.get("previous_url")
    # Split the string at "?" and extract the first part, then reattach the "?"
    previous_url = previous_url.split("?")[0] + "?"
    if "attendance-update" in previous_url:
        hx_target = "#updateAttendanceModalBody"
    elif "edit-validate-attendance" in previous_url:
        hx_target = "#editValidateAttendanceRequestModalBody"
    elif "request-attendance" in previous_url:
        hx_target = "#objectUpdateModalTarget"
    elif "attendance-create" in previous_url:
        hx_target = "#addAttendanceModalBody"
    else:
        hx_target = "#objectCreateModalTarget"
    if request.method == "POST":
        form = BatchAttendanceForm(request.POST)
        if form.is_valid():
            batch = form.save()
            messages.success(request, _("Attendance batch created successfully."))
            previous_form_data += f"&batch_attendance_id={batch.id}"
    return render(
        request,
        "attendance/attendance/batch_attendance_form.html",
        {
            "form": form,
            "previous_form_data": previous_form_data,
            "previous_url": previous_url,
            "hx_target": hx_target,
        },
    )


@login_required
def get_batches(request):
    batches = BatchAttendance.objects.all()
    return render(
        request, "attendance/attendance/batches_list.html", {"batches": batches}
    )


@login_required
def update_title(request):
    batch_id = request.POST.get("batch_id")
    try:
        batch = BatchAttendance.objects.filter(id=batch_id).first()
        if (
            request.user.has_perm("attendance.change_attendancegeneralsettings")
            or request.user == batch.created_by
        ):
            title = request.POST.get("title")
            batch.title = title
            batch.save()
            messages.success(request, _("Batch attendance title updated sucessfully."))
        else:
            messages.info(request, _("You don't have permission."))
    except:
        messages.error(request, _("Something went wrong."))
    return redirect(reverse("get-batches"))


@login_required
@permission_required("attendance.delete_batchattendance")
def delete_batch(request, batch_id):
    try:
        batch_name = BatchAttendance.objects.filter(id=batch_id).first().__str__()
        BatchAttendance.objects.filter(id=batch_id).first().delete()
        messages.success(
            request, _(f"{batch_name} - batch has been deleted sucessfully")
        )
    except ProtectedError as e:
        model_verbose_names_set = set()
        for obj in e.protected_objects:
            # Convert the lazy translation proxy to a string.
            model_verbose_names_set.add(str(_(obj._meta.verbose_name.capitalize())))
        model_names_str = ", ".join(model_verbose_names_set)
        messages.error(
            request,
            _("This {} is already in use for {}.").format(batch_name, model_names_str),
        ),
    except:
        messages.error(request, _("Something went wrong."))

    return redirect(reverse("get-batches"))


@login_required
def attendance_request_changes(request, attendance_id):
    """
    This method is used to store the requested changes to the instance
    """
    attendance = Attendance.objects.get(id=attendance_id)
    if request.GET.get("previous_url"):
        form = AttendanceRequestForm(initial=request.GET.dict())
    else:
        form = AttendanceRequestForm(instance=attendance)
        # form.fields["work_type_id"].widget.attrs.update(
        #     {
        #         "class": "w-100",
        #         "style": "height:50px;border-radius:0;border:1px solid hsl(213deg,22%,84%)",
        #     }
        # )
        # form.fields["shift_id"].widget.attrs.update(
        #     {
        #         "class": "w-100",
        #         "style": "height:50px;border-radius:0;border:1px solid hsl(213deg,22%,84%)",
        #     }
        # )
    if request.method == "POST":
        form = AttendanceRequestForm(request.POST, instance=copy.copy(attendance))
        form.fields["work_type_id"].widget.attrs.update(
            {
                "class": "w-100",
                "style": "height:50px;border-radius:0;border:1px solid hsl(213deg,22%,84%)",
            }
        )
        form.fields["shift_id"].widget.attrs.update(
            {
                "class": "w-100",
                "style": "height:50px;border-radius:0;border:1px solid hsl(213deg,22%,84%)",
            }
        )
        work_type_id = form.data["work_type_id"]
        shift_id = form.data["shift_id"]
        if work_type_id is None or not len(work_type_id):
            form.add_error("work_type_id", "This field is required")
        if shift_id is None or not len(shift_id):
            form.add_error("shift_id", "This field is required")
        if form.is_valid():
            # commit already set to False
            # so the changes not affected to the db
            instance = form.save()
            instance.employee_id = attendance.employee_id
            instance.id = attendance.id
            if attendance.request_type != "create_request":
                attendance.requested_data = json.dumps(instance.serialize())
                attendance.request_description = instance.request_description
                # set the user level validation here
                attendance.is_validate_request = True
                attendance.save()
            else:
                instance.is_validate_request_approved = False
                instance.is_validate_request = True
                instance.save()
            messages.success(request, _("Attendance update request created."))
            employee = attendance.employee_id
            if attendance.employee_id.employee_work_info.reporting_manager_id:
                reporting_manager = (
                    attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                )
                user_last_name = get_employee_last_name(attendance)
                notify.send(
                    request.user,
                    recipient=reporting_manager,
                    verb=f"{employee.employee_first_name} {user_last_name}'s\
                          attendance update request for {attendance.attendance_date} is created",
                    verb_ar=f"تم إنشاء طلب تحديث الحضور لـ {employee.employee_first_name} \
                        {user_last_name }في {attendance.attendance_date}",
                    verb_de=f"Die Anfrage zur Aktualisierung der Anwesenheit von \
                        {employee.employee_first_name} {user_last_name} \
                            für den {attendance.attendance_date} wurde erstellt",
                    verb_es=f"Se ha creado la solicitud de actualización de asistencia para {employee.employee_first_name}\
                          {user_last_name} el {attendance.attendance_date}",
                    verb_fr=f"La demande de mise à jour de présence de {employee.employee_first_name}\
                          {user_last_name} pour le {attendance.attendance_date} a été créée",
                    redirect=reverse("request-attendance-view")
                    + f"?id={attendance.id}",
                    icon="checkmark-circle-outline",
                )
            return HttpResponse(
                render(
                    request,
                    "requests/attendance/form.html",
                    {"form": form, "attendance_id": attendance_id},
                ).content.decode("utf-8")
                + "<script>location.reload();</script>"
            )
    return render(
        request,
        "requests/attendance/form.html",
        {"form": form, "attendance_id": attendance_id},
    )


@login_required
def validate_attendance_request(request, attendance_id):
    """
    This method to validate the requested attendance
    args:
        attendance_id : attendance id
    """
    attendance = Attendance.objects.get(id=attendance_id)
    first_dict = attendance.serialize()
    empty_data = {
        "employee_id": None,
        "attendance_date": None,
        "attendance_clock_in_date": None,
        "attendance_clock_in": None,
        "attendance_clock_out": None,
        "attendance_clock_out_date": None,
        "shift_id": None,
        "work_type_id": None,
        "attendance_worked_hour": None,
        "batch_attendance_id": None,
    }
    if attendance.request_type == "create_request":
        other_dict = first_dict
        first_dict = empty_data
    else:
        other_dict = json.loads(attendance.requested_data)
    requests_ids_json = request.GET.get("requests_ids")
    previous_instance_id = next_instance_id = attendance.pk
    if requests_ids_json:
        previous_instance_id, next_instance_id = closest_numbers(
            json.loads(requests_ids_json), attendance_id
        )
    return render(
        request,
        "requests/attendance/individual_view.html",
        {
            "data": get_diff_dict(first_dict, other_dict, Attendance),
            "attendance": attendance,
            "previous": previous_instance_id,
            "next": next_instance_id,
            "requests_ids": requests_ids_json,
        },
    )


@login_required
@manager_can_enter("attendance.change_attendance")
def approve_validate_attendance_request(request, attendance_id):
    """
    This method is used to validate the attendance requests
    """
    attendance = Attendance.objects.get(id=attendance_id)
    prev_attendance_date = attendance.attendance_date
    prev_attendance_clock_in_date = attendance.attendance_clock_in_date
    prev_attendance_clock_in = attendance.attendance_clock_in
    attendance.attendance_validated = True
    attendance.is_validate_request_approved = True
    attendance.is_validate_request = False
    attendance.request_description = None
    attendance.save()
    if attendance.requested_data is not None:
        requested_data = json.loads(attendance.requested_data)
        requested_data["attendance_clock_out"] = (
            None
            if requested_data["attendance_clock_out"] == "None"
            else requested_data["attendance_clock_out"]
        )
        requested_data["attendance_clock_out_date"] = (
            None
            if requested_data["attendance_clock_out_date"] == "None"
            else requested_data["attendance_clock_out_date"]
        )
        Attendance.objects.filter(id=attendance_id).update(**requested_data)
        # DUE TO AFFECT THE OVERTIME CALCULATION ON SAVE METHOD, SAVE THE INSTANCE ONCE MORE
        attendance = Attendance.objects.get(id=attendance_id)
        attendance.save()

    if (
        attendance.attendance_clock_out is None
        or attendance.attendance_clock_out_date is None
    ):
        attendance.attendance_validated = True
        activity = AttendanceActivity.objects.filter(
            employee_id=attendance.employee_id,
            attendance_date=prev_attendance_date,
            clock_in_date=prev_attendance_clock_in_date,
            clock_in=prev_attendance_clock_in,
        )
        if activity:
            activity.update(
                employee_id=attendance.employee_id,
                attendance_date=attendance.attendance_date,
                clock_in_date=attendance.attendance_clock_in_date,
                clock_in=attendance.attendance_clock_in,
            )

        else:
            AttendanceActivity.objects.create(
                employee_id=attendance.employee_id,
                attendance_date=attendance.attendance_date,
                clock_in_date=attendance.attendance_clock_in_date,
                clock_in=attendance.attendance_clock_in,
            )

    # Create late come or early out objects
    shift = attendance.shift_id
    day = attendance.attendance_date.strftime("%A").lower()
    day = EmployeeShiftDay.objects.get(day=day)

    minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
        day=day, shift=shift
    )
    if attendance.attendance_clock_in:
        late_come(
            attendance, start_time=start_time_sec, end_time=end_time_sec, shift=shift
        )
    if attendance.attendance_clock_out:
        early_out(
            attendance, start_time=start_time_sec, end_time=end_time_sec, shift=shift
        )

    messages.success(request, _("Attendance request has been approved"))
    employee = attendance.employee_id
    notify.send(
        request.user,
        recipient=employee.employee_user_id,
        verb=f"Your attendance request for \
            {attendance.attendance_date} is validated",
        verb_ar=f"تم التحقق من طلب حضورك في تاريخ \
            {attendance.attendance_date}",
        verb_de=f"Ihr Anwesenheitsantrag für das Datum \
            {attendance.attendance_date} wurde bestätigt",
        verb_es=f"Se ha validado su solicitud de asistencia \
            para la fecha {attendance.attendance_date}",
        verb_fr=f"Votre demande de présence pour la date \
            {attendance.attendance_date} est validée",
        redirect=reverse("request-attendance-view") + f"?id={attendance.id}",
        icon="checkmark-circle-outline",
    )
    if attendance.employee_id.employee_work_info.reporting_manager_id:
        reporting_manager = (
            attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id
        )
        user_last_name = get_employee_last_name(attendance)
        notify.send(
            request.user,
            recipient=reporting_manager,
            verb=f"{employee.employee_first_name} {user_last_name}'s\
                  attendance request for {attendance.attendance_date} is validated",
            verb_ar=f"تم التحقق من طلب الحضور لـ {employee.employee_first_name} \
                {user_last_name} في {attendance.attendance_date}",
            verb_de=f"Die Anwesenheitsanfrage von {employee.employee_first_name} \
                {user_last_name} für den {attendance.attendance_date} wurde validiert",
            verb_es=f"Se ha validado la solicitud de asistencia de \
                {employee.employee_first_name} {user_last_name} para el {attendance.attendance_date}",
            verb_fr=f"La demande de présence de {employee.employee_first_name} \
                {user_last_name} pour le {attendance.attendance_date} a été validée",
            redirect=reverse("request-attendance-view") + f"?id={attendance.id}",
            icon="checkmark-circle-outline",
        )
    return HorillaRedirect(request)


@login_required
def cancel_attendance_request(request, attendance_id):
    """
    This method is used to cancel attendance request
    """
    try:
        attendance = Attendance.objects.get(id=attendance_id)
        if (
            attendance.employee_id.employee_user_id == request.user
            or is_reportingmanager(request)
            or request.user.has_perm("attendance.change_attendance")
        ):
            attendance.is_validate_request_approved = False
            attendance.is_validate_request = False
            attendance.request_description = None
            attendance.requested_data = None
            attendance.request_type = None

            attendance.save()
            if attendance.request_type == "create_request":
                attendance.delete()
                messages.success(request, _("The requested attendance is removed."))
            else:
                messages.success(request, _("Attendance request has been rejected"))
            employee = attendance.employee_id
            notify.send(
                request.user,
                recipient=employee.employee_user_id,
                verb=f"Your attendance request for {attendance.attendance_date} is rejected",
                verb_ar=f"تم رفض طلبك للحضور في تاريخ {attendance.attendance_date}",
                verb_de=f"Ihre Anwesenheitsanfrage für {attendance.attendance_date} wurde abgelehnt",
                verb_es=f"Tu solicitud de asistencia para el {attendance.attendance_date} ha sido rechazada",
                verb_fr=f"Votre demande de présence pour le {attendance.attendance_date} est rejetée",
                icon="close-circle-outline",
            )
    except (Attendance.DoesNotExist, OverflowError):
        messages.error(request, _("Attendance request not found"))
    return HorillaRedirect(request)


@login_required
def select_all_filter_attendance_request(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendance"):
            employee_filter = AttendanceFilters(
                request.GET,
                queryset=Attendance.objects.filter(is_validate_request=True),
            )
        else:
            employee_filter = AttendanceFilters(
                request.GET,
                queryset=Attendance.objects.filter(
                    employee_id__employee_user_id=request.user, is_validate_request=True
                )
                | Attendance.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user,
                    is_validate_request=True,
                ),
            )

        # Get the filtered queryset

        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@manager_can_enter("attendance.change_attendance")
def bulk_approve_attendance_request(request):
    """
    This method is used to validate the attendance requests
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for attendance_id in ids:
        attendance = Attendance.objects.get(id=attendance_id)
        prev_attendance_date = attendance.attendance_date
        prev_attendance_clock_in_date = attendance.attendance_clock_in_date
        prev_attendance_clock_in = attendance.attendance_clock_in
        attendance.attendance_validated = True
        attendance.is_validate_request_approved = True
        attendance.is_validate_request = False
        attendance.request_description = None
        attendance.save()
        if attendance.requested_data is not None:
            requested_data = json.loads(attendance.requested_data)
            requested_data["attendance_clock_out"] = (
                None
                if requested_data["attendance_clock_out"] == "None"
                else requested_data["attendance_clock_out"]
            )
            requested_data["attendance_clock_out_date"] = (
                None
                if requested_data["attendance_clock_out_date"] == "None"
                else requested_data["attendance_clock_out_date"]
            )
            Attendance.objects.filter(id=attendance_id).update(**requested_data)
            # DUE TO AFFECT THE OVERTIME CALCULATION ON SAVE METHOD, SAVE THE INSTANCE ONCE MORE
            attendance = Attendance.objects.get(id=attendance_id)
            attendance.save()
        if (
            attendance.attendance_clock_out is None
            or attendance.attendance_clock_out_date is None
        ):
            attendance.attendance_validated = True
            activity = AttendanceActivity.objects.filter(
                employee_id=attendance.employee_id,
                attendance_date=prev_attendance_date,
                clock_in_date=prev_attendance_clock_in_date,
                clock_in=prev_attendance_clock_in,
            )
            if activity:
                activity.update(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance.attendance_date,
                    clock_in_date=attendance.attendance_clock_in_date,
                    clock_in=attendance.attendance_clock_in,
                )

            else:
                AttendanceActivity.objects.create(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance.attendance_date,
                    clock_in_date=attendance.attendance_clock_in_date,
                    clock_in=attendance.attendance_clock_in,
                )

        # Create late come or early out objects
        shift = attendance.shift_id
        day = attendance.attendance_date.strftime("%A").lower()
        day = EmployeeShiftDay.objects.get(day=day)

        minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
            day=day, shift=shift
        )
        if attendance.attendance_clock_in:
            late_come(
                attendance,
                start_time=start_time_sec,
                end_time=end_time_sec,
                shift=shift,
            )
        if attendance.attendance_clock_out:
            early_out(
                attendance,
                start_time=start_time_sec,
                end_time=end_time_sec,
                shift=shift,
            )

        messages.success(request, _("Attendance request has been approved"))
        employee = attendance.employee_id
        notify.send(
            request.user,
            recipient=employee.employee_user_id,
            verb=f"Your attendance request for \
                {attendance.attendance_date} is validated",
            verb_ar=f"تم التحقق من طلب حضورك في تاريخ \
                {attendance.attendance_date}",
            verb_de=f"Ihr Anwesenheitsantrag für das Datum \
                {attendance.attendance_date} wurde bestätigt",
            verb_es=f"Se ha validado su solicitud de asistencia \
                para la fecha {attendance.attendance_date}",
            verb_fr=f"Votre demande de présence pour la date \
                {attendance.attendance_date} est validée",
            redirect=reverse("request-attendance-view") + f"?id={attendance.id}",
            icon="checkmark-circle-outline",
        )
        if attendance.employee_id.employee_work_info.reporting_manager_id:
            reporting_manager = (
                attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id
            )
            user_last_name = get_employee_last_name(attendance)
            notify.send(
                request.user,
                recipient=reporting_manager,
                verb=f"{employee.employee_first_name} {user_last_name}'s\
                    attendance request for {attendance.attendance_date} is validated",
                verb_ar=f"تم التحقق من طلب الحضور لـ {employee.employee_first_name} \
                    {user_last_name} في {attendance.attendance_date}",
                verb_de=f"Die Anwesenheitsanfrage von {employee.employee_first_name} \
                    {user_last_name} für den {attendance.attendance_date} wurde validiert",
                verb_es=f"Se ha validado la solicitud de asistencia de \
                    {employee.employee_first_name} {user_last_name} para el {attendance.attendance_date}",
                verb_fr=f"La demande de présence de {employee.employee_first_name} \
                    {user_last_name} pour le {attendance.attendance_date} a été validée",
                redirect=reverse("request-attendance-view") + f"?id={attendance.id}",
                icon="checkmark-circle-outline",
            )
    return HttpResponse("success")


@login_required
@manager_can_enter("attendance.delete_attendance")
def bulk_reject_attendance_request(request):
    """
    This method is used to delete bulk attendance request
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for attendance_id in ids:
        try:
            attendance = Attendance.objects.get(id=attendance_id)
            if (
                attendance.employee_id.employee_user_id == request.user
                or is_reportingmanager(request)
                or request.user.has_perm("attendance.change_attendance")
            ):
                attendance.is_validate_request_approved = False
                attendance.is_validate_request = False
                attendance.request_description = None
                attendance.requested_data = None
                attendance.request_type = None
                attendance.save()
                if attendance.request_type == "create_request":
                    attendance.delete()
                    messages.success(request, _("The requested attendance is removed."))
                else:
                    messages.success(
                        request, _("The requested attendance is rejected.")
                    )
                employee = attendance.employee_id
                notify.send(
                    request.user,
                    recipient=employee.employee_user_id,
                    verb=f"Your attendance request for {attendance.attendance_date} is rejected",
                    verb_ar=f"تم رفض طلبك للحضور في تاريخ {attendance.attendance_date}",
                    verb_de=f"Ihre Anwesenheitsanfrage für {attendance.attendance_date} wurde abgelehnt",
                    verb_es=f"Tu solicitud de asistencia para el {attendance.attendance_date} ha sido rechazada",
                    verb_fr=f"Votre demande de présence pour le {attendance.attendance_date} est rejetée",
                    icon="close-circle-outline",
                )
        except (Attendance.DoesNotExist, OverflowError):
            messages.error(request, _("Attendance request not found"))
    return HttpResponse("success")


@login_required
@manager_can_enter("attendance.change_attendance")
def edit_validate_attendance(request, attendance_id):
    """
    This method is used to edit and update the validate request attendance
    """
    attendance = Attendance.objects.get(id=attendance_id)
    initial = attendance.serialize()
    if request.GET.get("previous_url"):
        initial = request.GET.dict()
    else:
        if attendance.request_type != "create_request":
            initial = json.loads(attendance.requested_data)
        initial["request_description"] = attendance.request_description
    form = AttendanceRequestForm(initial=initial)
    form.instance.id = attendance.id
    hx_target = request.META.get("HTTP_HX_TARGET")
    if request.method == "POST":
        form = AttendanceRequestForm(request.POST, instance=copy.copy(attendance))
        if form.is_valid():
            instance = form.save()
            instance.employee_id = attendance.employee_id
            instance.id = attendance.id
            if attendance.request_type != "create_request":
                attendance.requested_data = json.dumps(instance.serialize())
                attendance.request_description = instance.request_description
                # set the user level validation here
                attendance.is_validate_request = True
                attendance.save()
            else:
                instance.is_validate_request_approved = False
                instance.is_validate_request = True
                instance.save()
            return HttpResponse(
                f"""
                                <script>
                                $('#editValidateAttendanceRequest').removeClass('oh-modal--show');
                                $('[data-target="#validateAttendanceRequest"][data-attendance-id={attendance.id}]').click();
                                $('#messages').html(
                                `
                                <div class="oh-alert-container">
                                <div class="oh-alert oh-alert--animated oh-alert--success">
                                Attendance request updated.
                                </div>
                                </div>
                                `
                                )
                                </script>
                                """
            )
    return render(
        request,
        "requests/attendance/update_form.html",
        {"form": form, "hx_target": hx_target},
    )


@login_required
@hx_request_required
def get_employee_shift(request):
    """
    method used to get employee shift
    """
    employee_id = request.GET.get("employee_id")
    shift = None
    if employee_id:
        employee = Employee.objects.get(id=employee_id)
        shift = employee.get_shift
    form = NewRequestForm()
    if request.GET.get("bulk") and eval_validate(request.GET.get("bulk")):
        form = BulkAttendanceRequestForm()
    form.fields["shift_id"].queryset = EmployeeShift.objects.all()
    form.fields["shift_id"].widget.attrs["hx-trigger"] = "load,change"
    form.fields["shift_id"].initial = shift
    shift_id = render_to_string(
        "requests/attendance/form_field.html",
        {
            "field": form["shift_id"],
            "shift": shift,
        },
    )
    return HttpResponse(f"{shift_id}")
