"""
models.py

This module is used to register models for recruitment app

"""

import contextlib
import datetime as dt
import json
from datetime import date, datetime, timedelta

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from attendance.methods.utils import (
    MONTH_MAPPING,
    attendance_date_validate,
    format_time,
    get_diff_dict,
    strtime_seconds,
    validate_hh_mm_ss_format,
    validate_time_format,
    validate_time_in_minutes,
)
from base.horilla_company_manager import HorillaCompanyManager
from base.methods import is_company_leave, is_holiday
from base.models import Company, EmployeeShift, EmployeeShiftDay, WorkType
from employee.models import Employee
from horilla.methods import get_horilla_model_class
from horilla.models import HorillaModel, upload_path
from horilla_audit.models import HorillaAuditInfo, HorillaAuditLog

# to skip the migration issue with the old migrations
_validate_time_in_minutes = validate_time_in_minutes


# Create your models here.


class AttendanceActivity(HorillaModel):
    """
    AttendanceActivity model
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="employee_attendance_activities",
        verbose_name=_("Employee"),
    )
    attendance_date = models.DateField(
        null=True,
        validators=[attendance_date_validate],
        verbose_name=_("Attendance Date"),
    )
    PUNCH_SOURCE_CHOICES = [
        ("app", _("App")),
        ("biometric", _("Biometric Device")),
    ]
    shift_day = models.ForeignKey(
        EmployeeShiftDay,
        null=True,
        on_delete=models.DO_NOTHING,
        verbose_name=_("Shift Day"),
    )
    in_datetime = models.DateTimeField(null=True)
    clock_in_date = models.DateField(null=True, verbose_name=_("In Date"))
    clock_in = models.TimeField(verbose_name=_("Check In"))
    clock_in_source = models.CharField(
        max_length=10,
        choices=PUNCH_SOURCE_CHOICES,
        default="app",
        verbose_name=_("Check-In Source"),
    )
    clock_out_date = models.DateField(null=True, verbose_name=_("Out Date"))
    out_datetime = models.DateTimeField(null=True)
    clock_out = models.TimeField(null=True, verbose_name=_("Check Out"))
    clock_out_source = models.CharField(
        max_length=10,
        choices=PUNCH_SOURCE_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Check-Out Source"),
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        """
        Meta class to add some additional options
        """

        ordering = ["-attendance_date", "employee_id__employee_first_name", "clock_in"]

    def duration(self):
        """
        Duration calc b/w in-out method
        """

        if not self.clock_out or not self.clock_out_date:
            self.clock_out_date = datetime.today().date()
            self.clock_out = datetime.now().time()

        clock_in_datetime = datetime.combine(self.clock_in_date, self.clock_in)
        clock_out_datetime = datetime.combine(self.clock_out_date, self.clock_out)

        time_difference = clock_out_datetime - clock_in_datetime

        return time_difference.total_seconds()

    def __str__(self):
        return f"{self.employee_id} - {self.attendance_date} - {self.clock_in} - {self.clock_out}"


class BatchAttendance(HorillaModel):
    """
    Batch attendance model
    """

    title = models.CharField(max_length=150, verbose_name=_("Title"))

    def __str__(self):
        return f"{self.title}-{self.id}"


class Attendance(HorillaModel):
    """
    Attendance model
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    status = [
        ("create_request", _("Create Request")),
        ("update_request", _("Update Request")),
        ("revalidate_request", _("Re-validate Request")),
    ]

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        related_name="employee_attendances",
        verbose_name=_("Employee"),
    )
    attendance_date = models.DateField(
        null=False,
        validators=[attendance_date_validate],
        verbose_name=_("Attendance date"),
    )
    shift_id = models.ForeignKey(
        EmployeeShift, on_delete=models.SET_NULL, null=True, verbose_name=_("Shift")
    )
    work_type_id = models.ForeignKey(
        WorkType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # 796
        verbose_name=_("Work Type"),
    )
    attendance_day = models.ForeignKey(
        EmployeeShiftDay,
        on_delete=models.DO_NOTHING,
        null=True,
        verbose_name=_("Attendance day"),
    )
    attendance_clock_in_date = models.DateField(
        null=True, verbose_name=_("Check-In Date")
    )
    attendance_clock_in = models.TimeField(
        null=True, verbose_name=_("Check-In"), help_text=_("First Check-In Time")
    )
    attendance_clock_out_date = models.DateField(
        null=True, verbose_name=_("Check-Out Date")
    )
    attendance_clock_out = models.TimeField(
        null=True, verbose_name=_("Check-Out"), help_text=_("Last Check-Out Time")
    )
    attendance_worked_hour = models.CharField(
        null=True,
        default="00:00",
        max_length=10,
        validators=[validate_time_format],
        verbose_name=_("Worked Hours"),
    )
    minimum_hour = models.CharField(
        max_length=10,
        default="00:00",
        validators=[validate_time_format],
        verbose_name=_("Minimum hour"),
    )
    batch_attendance_id = models.ForeignKey(
        BatchAttendance,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name=_("Batch Attendance"),
    )
    attendance_overtime = models.CharField(
        default="00:00",
        validators=[validate_time_format],
        max_length=10,
        verbose_name=_("Overtime"),
    )
    attendance_overtime_approve = models.BooleanField(
        default=False, verbose_name=_("Overtime Approve")
    )
    attendance_validated = models.BooleanField(
        default=False, verbose_name=_("Attendance Validate")
    )
    at_work_second = models.IntegerField(null=True, blank=True)
    overtime_second = models.IntegerField(
        null=True, blank=True, verbose_name=_("Overtime In Second")
    )
    approved_overtime_second = models.IntegerField(default=0)
    is_validate_request = models.BooleanField(
        default=False, verbose_name=_("Is validate request")
    )
    is_bulk_request = models.BooleanField(default=False, editable=False)
    is_validate_request_approved = models.BooleanField(
        default=False, verbose_name=_("Is validate request approved")
    )
    request_description = models.TextField(
        null=True, verbose_name=_("Request Description")
    )
    request_type = models.CharField(
        max_length=18, null=True, choices=status, default="update_request"
    )
    ATTENDANCE_LOCATION_CHOICES = [
        ("office", _("Office")),
        ("wfh", _("Work From Home")),
    ]
    attendance_location = models.CharField(
        max_length=6,
        choices=ATTENDANCE_LOCATION_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Attendance Location"),
        help_text=_(
            "Automatically set based on punch source. "
            "Office requires both check-in and check-out via biometric device."
        ),
    )
    is_holiday = models.BooleanField(default=False)
    requested_data = models.JSONField(null=True, editable=False)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("Approved By"),
        editable=False,
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
    )

    class Meta:
        """
        Meta class to add some additional options
        """

        unique_together = ("employee_id", "attendance_date")
        permissions = [
            ("change_validateattendance", "Validate Attendance"),
            ("change_approveovertime", "Change Approve Overtime"),
        ]
        ordering = [
            "-attendance_date",
            "employee_id__employee_first_name",
            "attendance_clock_in",
        ]
        verbose_name = _("Attendance")
        verbose_name_plural = _("Attendances")

    def check_min_ot(self):
        """
        Method to check the min ot for the attendance
        """

    def is_night_shift(self):
        """
        check is night shift or not
        """
        day = self.attendance_day
        if day is None:
            return False
        schedule = day.day_schedule.filter(shift_id=self.shift_id).first()
        if not schedule:
            return False
        return schedule.is_night_shift

    def __str__(self) -> str:
        return f"{self.employee_id.employee_first_name} \
            {self.employee_id.employee_last_name} - {self.attendance_date}"

    def activities(self):
        """
        This method is used to return the activites and count of activites comes for an attendance
        """
        activities = AttendanceActivity.objects.filter(
            attendance_date=self.attendance_date, employee_id=self.employee_id
        )
        return {"query": activities, "count": activities.count()}

    def requested_fields(self):
        """
        This method will returns the value difference fields
        """
        keys = []
        if self.requested_data is not None:
            data = json.loads(self.requested_data)
            diffs = get_diff_dict(self.serialize(), data)
            keys = diffs.keys()
        return keys

    def get_last_clock_out(self, null_activity=False):
        """
        This method is used to get the last attendance activity if exists
        """
        activities = AttendanceActivity.objects.filter(
            employee_id=self.employee_id,
            attendance_date=self.attendance_date,
            clock_out__isnull=null_activity,
        ).order_by("id")
        return activities.last()

    def get_at_work_from_activities(self):
        """
        This method is used to retun the at work calculated from the activities
        """
        activities = AttendanceActivity.objects.filter(
            attendance_date=self.attendance_date, employee_id=self.employee_id
        ).order_by("clock_in")
        at_work_seconds = 0
        now = datetime.now()
        for activity in activities:
            out_time = activity.clock_out
            if out_time is None:
                combined_out = datetime.combine(
                    now, dt.time(hour=now.hour, minute=now.minute, second=now.second)
                )
            else:
                combined_out = datetime.combine(activity.clock_out_date, out_time)
            in_time = activity.clock_in
            combined_in = datetime.combine(activity.clock_in_date, in_time)
            diffs = combined_out - combined_in
            at_work_seconds = at_work_seconds + diffs.total_seconds()
        return at_work_seconds

    def hours_pending(self):
        """
        This method will returns difference between minimum_hour and attendance_worked_hour
        """
        minimum_hours = strtime_seconds(self.minimum_hour)
        worked_hour = strtime_seconds(self.attendance_worked_hour)
        pending_seconds = minimum_hours - worked_hour
        if pending_seconds < 0:
            return "00:00"
        pending_hours = format_time(pending_seconds)
        return pending_hours

    def adjust_minimum_hour(self):
        """
        Set minimum_hour to 00:00 if the attendance date falls on a holiday or company leave.
        """
        if is_holiday(self.attendance_date) or is_company_leave(self.attendance_date):
            self.minimum_hour = "00:00"
            self.is_holiday = True

    def update_attendance_overtime(self):
        """
        Calculate and update attendance overtime and worked seconds.
        """
        self.attendance_overtime = format_time(
            max(
                0,
                (
                    strtime_seconds(self.attendance_worked_hour)
                    - strtime_seconds(self.minimum_hour)
                ),
            )
        )
        self.at_work_second = strtime_seconds(self.attendance_worked_hour)
        self.overtime_second = strtime_seconds(self.attendance_overtime)

    def handle_overtime_conditions(self):
        condition = AttendanceValidationCondition.objects.first()
        if self.is_validate_request:
            self.is_validate_request_approved = self.attendance_validated = False

        if condition:
            # Handle overtime cutoff
            if condition.overtime_cutoff:
                cutoff_seconds = strtime_seconds(condition.overtime_cutoff)
                if self.overtime_second > cutoff_seconds:
                    self.overtime_second = cutoff_seconds
                    self.attendance_overtime = format_time(cutoff_seconds)

            # Auto-approve overtime if conditions are met
            if condition.auto_approve_ot and self.overtime_second >= strtime_seconds(
                condition.minimum_overtime_to_approve
            ):
                self.attendance_overtime_approve = True

    def save(self, *args, **kwargs):
        self.update_attendance_overtime()
        self.attendance_day = EmployeeShiftDay.objects.get(
            day=self.attendance_date.strftime("%A").lower()
        )
        prev_attendance_approved = False
        self.adjust_minimum_hour()

        # Handle overtime cutoff and auto-approval
        self.handle_overtime_conditions()

        if self.pk is not None:
            # Get the previous values of the boolean field
            prev_state = Attendance.objects.get(pk=self.pk)
            prev_attendance_approved = prev_state.attendance_overtime_approve

        # super().save(*args, **kwargs)  #commend this line, it take too much time to complete
        employee_ot = self.employee_id.employee_overtime.filter(
            month=self.attendance_date.strftime("%B").lower(),
            year=self.attendance_date.year,
        ).first()
        if employee_ot:
            # Update if exists
            self.update_ot(employee_ot)
        else:
            # Create and update in one call
            employee_ot = self.create_ot()
            self.update_ot(employee_ot)
        approved = self.attendance_overtime_approve
        attendance_account = self.employee_id.employee_overtime.filter(
            month=self.attendance_date.strftime("%B").lower(),
            year=self.attendance_date.year,
        ).first()
        total_ot_seconds = attendance_account.overtime_second
        if approved and prev_attendance_approved is False:
            self.approved_overtime_second = self.overtime_second
            total_ot_seconds = total_ot_seconds + self.approved_overtime_second
        elif not approved:
            total_ot_seconds = total_ot_seconds - self.approved_overtime_second
            self.approved_overtime_second = 0
        attendance_account.overtime = format_time(total_ot_seconds)
        attendance_account.save()
        super().save(*args, **kwargs)

    def serialize(self):
        """
        Used to serialize attendance instance
        """
        # Return a dictionary containing the data you want to store
        # strftime("%d %b %Y") date
        # strftime("%I:%M %p") time
        serialized_data = {
            "employee_id": self.employee_id.id,
            "attendance_date": str(self.attendance_date),
            "attendance_clock_in_date": str(self.attendance_clock_in_date),
            "attendance_clock_in": str(self.attendance_clock_in),
            "attendance_clock_out": str(self.attendance_clock_out),
            "attendance_clock_out_date": str(self.attendance_clock_out_date),
            "shift_id": self.shift_id.id if self.shift_id else "",
            "work_type_id": self.work_type_id.id if self.work_type_id else "",
            "attendance_worked_hour": self.attendance_worked_hour,
            "minimum_hour": self.minimum_hour,
            "batch_attendance_id": (
                self.batch_attendance_id.id if self.batch_attendance_id else ""
            ),
            # Add other fields you want to store
        }
        return serialized_data

    def delete(self, *args, **kwargs):
        # Custom delete logic
        # Perform additional operations before deleting the object
        with contextlib.suppress(Exception):
            AttendanceActivity.objects.filter(
                attendance_date=self.attendance_date, employee_id=self.employee_id
            ).delete()
            employee_ot = self.employee_id.employee_overtime.filter(
                month=self.attendance_date.strftime("%B").lower(),
                year=self.attendance_date.strftime("%Y"),
            )
        if employee_ot.exists():
            self.update_ot(employee_ot.first())
        # Call the superclass delete() method to delete the object
        super().delete(*args, **kwargs)

        # Perform additional operations after deleting the object

    def create_ot(self):
        """
        Create a new Hour Account instance if it doesn't exist for a specific month and year.
        Returns:
            AttendanceOverTime: The created or fetched AttendanceOverTime instance.
        """
        # Create or fetch the AttendanceOverTime instance
        employee_ot, created = AttendanceOverTime.objects.get_or_create(
            employee_id=self.employee_id,
            month=self.attendance_date.strftime("%B").lower(),
            year=self.attendance_date.year,
        )

        # Update only if the fields are available
        if self.attendance_overtime_approve:
            employee_ot.overtime = self.attendance_overtime

        if self.attendance_validated:
            employee_ot.hour_account = self.attendance_worked_hour

        employee_ot.save()
        return employee_ot

    def update_ot(self, employee_ot):
        """
        Update the hour account for the given employee.

        Args:
            employee_ot (obj): AttendanceOverTime instance
        """
        if apps.is_installed("leave"):
            approved_leave_requests = self.employee_id.leaverequest_set.filter(
                start_date__lte=self.attendance_date,
                end_date__gte=self.attendance_date,
                status="approved",
            )
        else:
            approved_leave_requests = []

        # Separate hourly leave requests from full-day leave requests
        hourly_leave_by_date = {}
        exclude_condition = Q()
        if approved_leave_requests:
            for leave in approved_leave_requests:
                if (
                    hasattr(leave.leave_type_id, "is_hourly_leave")
                    and leave.leave_type_id.is_hourly_leave
                    and leave.requested_hours
                ):
                    # Accumulate hourly leave seconds per date
                    leave_date = leave.start_date
                    hourly_seconds = int(leave.requested_hours * 3600)
                    hourly_leave_by_date[leave_date] = (
                        hourly_leave_by_date.get(leave_date, 0) + hourly_seconds
                    )
                else:
                    # Full-day leave: exclude entire day as before
                    exclude_condition |= Q(
                        attendance_date__range=(leave.start_date, leave.end_date)
                    )

        # Also collect hourly leaves for the entire month (not just today)
        if apps.is_installed("leave"):
            month_hourly_leaves = self.employee_id.leaverequest_set.filter(
                start_date__month=self.attendance_date.month,
                start_date__year=self.attendance_date.year,
                status="approved",
                leave_type_id__is_hourly_leave=True,
                requested_hours__gt=0,
            )
            for leave in month_hourly_leaves:
                leave_date = leave.start_date
                hourly_seconds = int(leave.requested_hours * 3600)
                hourly_leave_by_date[leave_date] = (
                    hourly_leave_by_date.get(leave_date, 0) + hourly_seconds
                )

        # Filter month attendances in a single query
        month_attendances = (
            Attendance.objects.filter(
                employee_id=self.employee_id,
                attendance_date__month=self.attendance_date.month,
                attendance_date__year=self.attendance_date.year,
                attendance_validated=True,
            )
            .exclude(exclude_condition)
            .values("minimum_hour", "at_work_second", "attendance_date")
        )

        # Calculate hour balance and hours pending in a single loop
        hour_balance = 0
        minimum_hour_second = 0
        for attendance in month_attendances:
            required_work_second = strtime_seconds(attendance["minimum_hour"])
            # Reduce minimum hour by hourly leave taken on this date
            att_date = attendance["attendance_date"]
            if att_date in hourly_leave_by_date:
                required_work_second = max(
                    0, required_work_second - hourly_leave_by_date[att_date]
                )
            at_work_second = min(required_work_second, attendance["at_work_second"])
            hour_balance += at_work_second
            minimum_hour_second += required_work_second

        hours_pending = minimum_hour_second - hour_balance
        employee_ot.worked_hours = format_time(hour_balance)
        employee_ot.pending_hours = format_time(hours_pending)
        employee_ot.save()

        return employee_ot

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        now = datetime.now().time()
        today = datetime.today().date()

        # Convert to time if it's a string
        if isinstance(self.attendance_clock_out, str):
            out_time = datetime.strptime(self.attendance_clock_out, "%H:%M:%S").time()
        else:
            out_time = self.attendance_clock_out

        if self.attendance_clock_in_date < self.attendance_date:
            raise ValidationError(
                {
                    "attendance_clock_in_date": "Attendance check-in date cannot be earlier than attendance date"
                }
            )

        if (
            self.attendance_clock_out_date
            and self.attendance_clock_out_date < self.attendance_clock_in_date
        ):
            raise ValidationError(
                {
                    "attendance_clock_out_date": "Attendance check-out date cannot be earlier than check-in date"
                }
            )

        if self.attendance_clock_out_date and self.attendance_clock_out_date >= today:
            if out_time > now:
                raise ValidationError(
                    {"attendance_clock_out": "Check-out time cannot be in the future"}
                )


class AttendanceRequestFile(HorillaModel):
    file = models.FileField(upload_to=upload_path)


class AttendanceRequestComment(HorillaModel):
    """
    AttendanceRequestComment Model
    """

    request_id = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    employee_id = models.ForeignKey(Employee, on_delete=models.CASCADE)
    files = models.ManyToManyField(AttendanceRequestFile, blank=True)
    comment = models.TextField(null=True, verbose_name=_("Comment"), max_length=255)

    def __str__(self) -> str:
        return f"{self.comment}"


class AttendanceOverTime(HorillaModel):
    """
    AttendanceOverTime model
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="employee_overtime",
        verbose_name=_("Employee"),
    )
    month = models.CharField(
        max_length=10,
        verbose_name=_("Month"),
    )
    month_sequence = models.PositiveSmallIntegerField(default=0)
    year = models.CharField(
        default=datetime.now().strftime("%Y"),
        null=True,
        max_length=10,
        verbose_name=_("Year"),
    )
    worked_hours = models.CharField(
        max_length=10,
        default="00:00",
        null=True,
        validators=[validate_time_format],
        verbose_name=_("Worked Hours"),
    )
    pending_hours = models.CharField(
        max_length=10,
        default="00:00",
        null=True,
        validators=[validate_time_format],
        verbose_name=_("Pending Hours"),
    )
    overtime = models.CharField(
        max_length=20,
        default="00:00",
        validators=[validate_time_format],
        verbose_name=_("Overtime Hours"),
    )
    hour_account_second = models.IntegerField(
        default=0,
        null=True,
        verbose_name=_("Worked Seconds"),
    )
    hour_pending_second = models.IntegerField(
        default=0,
        null=True,
        verbose_name=_("Pending Seconds"),
    )
    overtime_second = models.IntegerField(
        default=0,
        null=True,
        verbose_name=_("Overtime Seconds"),
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        """
        Meta class to add some additional options
        """

        unique_together = [("employee_id"), ("month"), ("year")]
        ordering = ["-year", "-month_sequence"]
        verbose_name = _("Hour Account")
        verbose_name_plural = _("Hour Accounts")

    def clean(self):
        try:
            year = int(self.year)
            if not (1900 <= year <= 2100):
                raise ValidationError(
                    {"year": _("Year must be an integer value between 1900 and 2100")}
                )
        except (ValueError, TypeError):
            raise ValidationError(
                {"year": _("Year must be an integer value between 1900 and 2100")}
            )

    def month_days(self):
        """
        this method is used to create new AttendanceOvertime's instance if there
        is no existing for a specific month and year
        """
        month = self.month_sequence + 1
        year = int(self.year)
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        return start_date, end_date

    def not_validated_hrs(self):
        """
        This method will return not validated hours in a month
        """
        hrs_to_vlaidate = sum(
            list(
                Attendance.objects.filter(
                    attendance_date__month=MONTH_MAPPING[self.month],
                    attendance_date__year=self.year,
                    employee_id=self.employee_id,
                    attendance_validated=False,
                ).values_list("at_work_second", flat=True)
            )
        )
        return format_time(hrs_to_vlaidate)

    def not_approved_ot_hrs(self):
        """
        This method will return the overtime hours to be approved
        """
        hrs_to_approve = sum(
            list(
                Attendance.objects.filter(
                    attendance_date__month=MONTH_MAPPING[self.month],
                    attendance_date__year=self.year,
                    employee_id=self.employee_id,
                    attendance_validated=True,
                    attendance_overtime_approve=False,
                ).values_list("overtime_second", flat=True)
            )
        )
        return format_time(hrs_to_approve)

    def get_month_index(self):
        """
        This method will return the index of the month
        """
        return MONTH_MAPPING[self.month]

    def save(self, *args, **kwargs):
        self.hour_account_second = strtime_seconds(self.worked_hours)
        self.hour_pending_second = strtime_seconds(self.pending_hours)
        self.overtime_second = strtime_seconds(self.overtime)
        month_name = self.month.split("-")[0]
        months = [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
        self.month_sequence = months.index(month_name)
        super().save(*args, **kwargs)


class AttendanceLateComeEarlyOut(HorillaModel):
    """
    AttendanceLateComeEarlyOut model
    """

    choices = [
        ("late_come", _("Late Come")),
        ("early_out", _("Early Out")),
    ]

    attendance_id = models.ForeignKey(
        Attendance,
        on_delete=models.PROTECT,
        related_name="late_come_early_out",
        verbose_name=_("Attendance"),
    )
    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.DO_NOTHING,
        null=True,
        related_name="late_come_early_out",
        verbose_name=_("Employee"),
        editable=False,
    )
    type = models.CharField(max_length=20, choices=choices, verbose_name=_("Type"))
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    def get_penalties_count(self):
        """
        This method is used to return the total penalties in the late early instance
        """
        return self.penaltyaccounts_set.count()

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        self.employee_id = self.attendance_id.employee_id
        super().save(*args, **kwargs)

    class Meta:
        """
        Meta class to add some additional options
        """

        unique_together = [("attendance_id"), ("type")]
        ordering = ["-attendance_id__attendance_date"]

    def __str__(self) -> str:
        return f"{self.attendance_id.employee_id.employee_first_name} \
            {self.attendance_id.employee_id.employee_last_name} - {self.type}"


class AttendanceValidationCondition(HorillaModel):
    """
    AttendanceValidationCondition model
    """

    validation_at_work = models.CharField(
        max_length=10,
        validators=[validate_time_format],
        verbose_name=_("Worked Hours Auto Approve Till"),
    )
    minimum_overtime_to_approve = models.CharField(
        blank=True, null=True, max_length=10, validators=[validate_time_format]
    )
    overtime_cutoff = models.CharField(
        blank=True, null=True, max_length=10, validators=[validate_time_format]
    )
    auto_approve_ot = models.BooleanField(
        default=False, verbose_name=_("Auto Approve OT")
    )
    company_id = models.ManyToManyField(Company, blank=True, verbose_name=_("Company"))
    objects = HorillaCompanyManager()

    def clean(self):
        """
        This method is used to perform some custom validations
        """
        super().clean()
        if not self.id and AttendanceValidationCondition.objects.exists():
            raise ValidationError(_("You cannot add more conditions."))


class GraceTime(HorillaModel):
    """
    Model for saving Grace time
    """

    allowed_time = models.CharField(
        default="00:00:00",
        validators=[validate_hh_mm_ss_format],
        max_length=10,
        verbose_name=_("Allowed Time"),
    )
    allowed_time_in_secs = models.IntegerField()
    allowed_clock_in = models.BooleanField(
        default=True,
        help_text=_("Allcocate this grace time for Check-In Attendance"),
        verbose_name=_("Allowed Clock-In"),
    )
    allowed_clock_out = models.BooleanField(
        default=False,
        help_text=_("Allcocate this grace time for Check-Out Attendance"),
        verbose_name=_("Allowed Clock-Out"),
    )
    is_default = models.BooleanField(default=False)

    company_id = models.ManyToManyField(Company, blank=True, verbose_name=_("Company"))
    objects = HorillaCompanyManager()

    def __str__(self) -> str:
        return str(f"{self.allowed_time} - Hours")

    def clean(self):
        """
        This method is used to perform some custom validations
        """
        super().clean()
        if self.is_default:
            if GraceTime.objects.filter(is_default=True).exclude(id=self.id).exists():
                raise ValidationError(
                    _("There is already a default grace time that exists.")
                )

        allowed_time = self.allowed_time
        is_default = self.is_default
        exclude_default = not is_default

        if (
            GraceTime.objects.filter(allowed_time=allowed_time)
            .exclude(is_default=exclude_default)
            .exclude(id=self.id)
            .exists()
        ):
            raise ValidationError(
                {
                    "allowed_time": _(
                        "There is already an existing grace time with this allowed time."
                    )
                }
            )

    def save(self, *args, **kwargs):
        allowed_time = self.allowed_time
        hours, minutes, secs = allowed_time.split(":")

        hours_int = int(hours)
        minutes_int = int(minutes)
        secs_int = int(secs)

        hours_str = f"{hours_int:02d}"
        minutes_str = f"{minutes_int:02d}"
        secs_str = f"{secs_int:02d}"

        self.allowed_time = f"{hours_str}:{minutes_str}:{secs_str}"
        self.allowed_time_in_secs = hours_int * 3600 + minutes_int * 60 + secs_int
        super().save(*args, **kwargs)


class AttendanceGeneralSetting(HorillaModel):
    """
    AttendanceGeneralSettings
    """

    time_runner = models.BooleanField(default=True)
    enable_check_in = models.BooleanField(
        default=True,
        verbose_name=_("Enable Check in/Check out"),
        help_text=_(
            "Enabling this feature allows employees to record their attendance using the Check-In/Check-Out button."
        ),
    )
    company_id = models.ForeignKey(Company, on_delete=models.CASCADE, null=True)
    objects = HorillaCompanyManager()


class WorkRecords(models.Model):
    """
    WorkRecord Model
    """

    choices = [
        ("FDP", _("Present")),
        ("HDP", _("Half Day Present")),
        ("ABS", _("Absent")),
        ("HD", _("Holiday/Company Leave")),
        ("CONF", _("Conflict")),
        ("DFT", _("Draft")),
    ]

    record_name = models.CharField(max_length=250, null=True, blank=True)
    work_record_type = models.CharField(max_length=10, null=True, choices=choices)
    employee_id = models.ForeignKey(
        Employee, on_delete=models.CASCADE, verbose_name=_("Employee")
    )
    date = models.DateField(null=True, blank=True)
    at_work = models.CharField(
        null=True,
        blank=True,
        validators=[
            validate_time_format,
        ],
        default="00:00",
        max_length=10,
    )  # 841
    min_hour = models.CharField(
        null=True,
        blank=True,
        validators=[
            validate_time_format,
        ],
        default="00:00",
        max_length=10,
    )
    at_work_second = models.IntegerField(null=True, blank=True, default=0)
    min_hour_second = models.IntegerField(null=True, blank=True, default=0)
    note = models.TextField(max_length=255)
    message = models.CharField(max_length=30, null=True, blank=True)
    is_attendance_record = models.BooleanField(default=False)
    attendance_id = models.ForeignKey(
        Attendance, on_delete=models.SET_NULL, blank=True, null=True
    )
    is_leave_record = models.BooleanField(default=False)
    if apps.is_installed("leave"):
        leave_request_id = models.ForeignKey(
            "leave.LeaveRequest",
            on_delete=models.SET_NULL,
            blank=True,
            null=True,
        )
    shift_id = models.ForeignKey(
        EmployeeShift, on_delete=models.SET_NULL, blank=True, null=True
    )
    day_percentage = models.FloatField(default=0)
    last_update = models.DateTimeField(null=True, blank=True)
    objects = HorillaCompanyManager("employee_id__employee_work_info__company_id")

    def title_message(self):
        title_message = self.message
        if title_message == "Leave":
            if apps.is_installed("leave"):
                title_message += f" | {self.leave_request_id.leave_type_id}"
        return title_message

    def save(self, *args, **kwargs):
        self.last_update = timezone.now()

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not 0.0 <= self.day_percentage <= 1.0:
            raise ValidationError(_("Day percentage must be between 0.0 and 1.0"))

    def __str__(self):
        return (
            self.record_name
            if self.record_name is not None
            else f"{self.work_record_type}-{self.date}-{self.employee_id}"
        )

    class Meta:
        verbose_name = _("Work Record")
        verbose_name_plural = _("Work Records")
        # unique_together = ['date', 'employee_id']


class HybridAttendanceViolation(HorillaModel):
    """
    Tracks weekly hybrid shift compliance violations.
    Created by the scheduler every Monday for employees who did not meet
    their required office days in the previous week.
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="hybrid_violations",
        verbose_name=_("Employee"),
    )
    week_start_date = models.DateField(verbose_name=_("Week Start Date"))
    shift_id = models.ForeignKey(
        EmployeeShift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Shift"),
    )
    required_office_days = models.PositiveSmallIntegerField(
        verbose_name=_("Required Office Days")
    )
    actual_office_days = models.PositiveSmallIntegerField(
        default=0, verbose_name=_("Actual Office Days")
    )
    is_resolved = models.BooleanField(default=False, verbose_name=_("Resolved"))
    resolved_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_hybrid_violations",
        verbose_name=_("Resolved By"),
        editable=False,
    )
    note = models.TextField(
        null=True, blank=True, verbose_name=_("Note"), max_length=255
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        verbose_name = _("Hybrid Attendance Violation")
        verbose_name_plural = _("Hybrid Attendance Violations")
        unique_together = [("employee_id", "week_start_date")]
        ordering = ["-week_start_date", "employee_id__employee_first_name"]
        permissions = [
            (
                "resolve_hybridattendanceviolation",
                "Resolve Hybrid Attendance Violation",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee_id} — week of {self.week_start_date} "
            f"({self.actual_office_days}/{self.required_office_days} office days)"
        )

    @property
    def shortage(self):
        return max(0, self.required_office_days - self.actual_office_days)

    @property
    def next_week_start_date(self):
        return (
            self.week_start_date + timedelta(days=7) if self.week_start_date else None
        )


class AttendancePolicy(HorillaModel):
    """
    Standalone attendance policy that defines how many office days per week
    an employee is required to attend.  Decoupled from the shift schedule so
    that "when to work" (shift) and "how often to attend" (policy) are
    managed independently.
    """

    name = models.CharField(max_length=100, verbose_name=_("Policy Name"))
    description = models.TextField(
        null=True, blank=True, verbose_name=_("Description")
    )
    required_days_per_week = models.PositiveSmallIntegerField(
        verbose_name=_("Required Days Per Week"),
        help_text=_(
            "Minimum number of office-attendance days required per week (1-7)."
        ),
    )
    company_id = models.ManyToManyField(
        Company, blank=True, verbose_name=_("Company")
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))
    objects = HorillaCompanyManager()
    history = HorillaAuditLog(
        related_name="attendance_policy_history",
        bases=[
            HorillaAuditInfo,
        ],
    )

    class Meta:
        verbose_name = _("Attendance Policy")
        verbose_name_plural = _("Attendance Policies")
        ordering = ["name"]

    def clean(self):
        super().clean()
        if (
            self.required_days_per_week is not None
            and not 1 <= self.required_days_per_week <= 7
        ):
            raise ValidationError(
                _("Required days per week must be between 1 and 7.")
            )

    def __str__(self):
        return f"{self.name} ({self.required_days_per_week} days/week)"


class AttendancePolicyViolation(HorillaModel):
    """
    Tracks weekly attendance-policy compliance violations.
    Created by the scheduler every Monday for employees who did not meet
    their policy's required office days in the previous week.
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="policy_violations",
        verbose_name=_("Employee"),
    )
    policy_id = models.ForeignKey(
        AttendancePolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Policy"),
    )
    week_start_date = models.DateField(verbose_name=_("Week Start Date"))
    required_days = models.PositiveSmallIntegerField(
        verbose_name=_("Required Days"),
    )
    actual_days = models.PositiveSmallIntegerField(
        default=0, verbose_name=_("Actual Days")
    )
    is_resolved = models.BooleanField(default=False, verbose_name=_("Resolved"))
    resolved_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_policy_violations",
        verbose_name=_("Resolved By"),
        editable=False,
    )
    note = models.TextField(
        null=True, blank=True, verbose_name=_("Note"), max_length=255
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        verbose_name = _("Attendance Policy Violation")
        verbose_name_plural = _("Attendance Policy Violations")
        unique_together = [("employee_id", "week_start_date")]
        ordering = ["-week_start_date", "employee_id__employee_first_name"]
        permissions = [
            (
                "resolve_attendancepolicyviolation",
                "Resolve Attendance Policy Violation",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee_id} — week of {self.week_start_date} "
            f"({self.actual_days}/{self.required_days} office days)"
        )

    @property
    def shortage(self):
        return max(0, self.required_days - self.actual_days)

    @property
    def next_week_start_date(self):
        return (
            self.week_start_date + timedelta(days=7) if self.week_start_date else None
        )


class AttendanceShiftRequest(HorillaModel):
    """
    Separate request record for carrying one missed office day into next week.
    """

    class RequestKind(models.TextChoices):
        HYBRID = "hybrid", _("Hybrid")
        POLICY = "policy", _("Attendance Policy")

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="attendance_shift_requests",
        verbose_name=_("Employee"),
    )
    request_kind = models.CharField(
        max_length=20,
        choices=RequestKind.choices,
        verbose_name=_("Request Type"),
    )
    from_week_start_date = models.DateField(verbose_name=_("From Week Start Date"))
    to_week_start_date = models.DateField(verbose_name=_("To Week Start Date"))
    requested_days = models.PositiveSmallIntegerField(
        default=1, verbose_name=_("Requested Days")
    )
    description = models.TextField(
        null=True, blank=True, verbose_name=_("Description")
    )
    canceled = models.BooleanField(default=False, verbose_name=_("Canceled"))
    hybrid_violation_id = models.ForeignKey(
        "attendance.HybridAttendanceViolation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_requests",
        verbose_name=_("Hybrid Violation"),
    )
    policy_violation_id = models.ForeignKey(
        "attendance.AttendancePolicyViolation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_requests",
        verbose_name=_("Policy Violation"),
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        verbose_name = _("Attendance Shift Request")
        verbose_name_plural = _("Attendance Shift Requests")
        ordering = ["-from_week_start_date", "-id"]

    @staticmethod
    def get_current_week_start(reference_date=None):
        reference_date = reference_date or timezone.localdate()
        return reference_date - timedelta(days=reference_date.weekday())

    def clean(self):
        super().clean()
        current_week_start = self.get_current_week_start()
        if self.requested_days != 1:
            raise ValidationError(_("Only one attendance day can be shifted per week."))
        if self.from_week_start_date != current_week_start:
            raise ValidationError(
                _("Attendance shift requests can only be created for the current week.")
            )
        if self.to_week_start_date != self.from_week_start_date + timedelta(days=7):
            raise ValidationError(
                _("Shift requests can only be created for the next week.")
            )
        if AttendanceShiftRequest.objects.filter(
            employee_id=self.employee_id,
            canceled=False,
            from_week_start_date=self.from_week_start_date,
        ).exclude(id=self.id).exists():
            raise ValidationError(
                _("An attendance shift request already exists for this week.")
            )
        if AttendanceShiftRequest.objects.filter(
            employee_id=self.employee_id,
            canceled=False,
            to_week_start_date=self.from_week_start_date,
        ).exclude(id=self.id).exists():
            raise ValidationError(
                _(
                    "A similar attendance shift request already exists from the previous week."
                )
            )
        if self.request_kind == self.RequestKind.HYBRID and self.policy_violation_id:
            raise ValidationError(
                _("Hybrid attendance shift requests cannot link policy violations.")
            )
        if self.request_kind == self.RequestKind.POLICY and self.hybrid_violation_id:
            raise ValidationError(
                _("Policy attendance shift requests cannot link hybrid violations.")
            )

    def __str__(self):
        return (
            f"{self.employee_id} - {self.from_week_start_date} -> "
            f"{self.to_week_start_date}"
        )

    def request_status(self):
        return _("Canceled") if self.canceled else _("Requested")
