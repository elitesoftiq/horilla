import datetime
import sys

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings

from base.backends import logger


def check_hybrid_compliance():
    """
    Run every Monday to evaluate the previous week's hybrid shift compliance
    for all employees. Creates a HybridAttendanceViolation record for every
    employee who did not meet their required office days.
    """
    from attendance.models import Attendance, HybridAttendanceViolation
    from base.models import EmployeeShift
    from employee.models import Employee

    today = datetime.date.today()
    # Compute the previous Monday (week_start) and the preceding Sunday (week_end)
    days_since_monday = today.weekday()  # 0=Monday
    week_start = today - datetime.timedelta(days=days_since_monday + 7)
    week_end = week_start + datetime.timedelta(days=6)

    hybrid_shifts = EmployeeShift.objects.filter(is_hybrid=True, is_active=True)
    if not hybrid_shifts.exists():
        return

    employees = Employee.objects.filter(
        employee_work_info__shift_id__in=hybrid_shifts,
        is_active=True,
    ).select_related("employee_work_info__shift_id")

    for employee in employees:
        shift = employee.employee_work_info.shift_id
        if not shift or not shift.is_hybrid or not shift.office_days_per_week:
            continue

        office_days = Attendance.objects.filter(
            employee_id=employee,
            attendance_date__gte=week_start,
            attendance_date__lte=week_end,
            attendance_location="office",
        ).count()

        if office_days < shift.office_days_per_week:
            HybridAttendanceViolation.objects.get_or_create(
                employee_id=employee,
                week_start_date=week_start,
                defaults={
                    "shift_id": shift,
                    "required_office_days": shift.office_days_per_week,
                    "actual_office_days": office_days,
                },
            )
        else:
            # If a previously created violation now shows compliance (manual
            # attendance corrections), update the actual count.
            HybridAttendanceViolation.objects.filter(
                employee_id=employee,
                week_start_date=week_start,
            ).update(actual_office_days=office_days)


def create_work_record():
    from attendance.models import WorkRecords
    from employee.models import Employee

    date = datetime.datetime.today()
    work_records = WorkRecords.objects.filter(date=date).values_list(
        "employee_id", flat=True
    )
    employees = Employee.objects.exclude(id__in=work_records)
    records_to_create = []

    for employee in employees:
        try:
            shift_schedule = employee.get_shift_schedule()
            if shift_schedule is None:
                continue

            shift = employee.get_shift()
            record = WorkRecords(
                employee_id=employee,
                date=date,
                work_record_type="DFT",
                shift_id=shift,
                message="",
            )
            records_to_create.append(record)
        except Exception as e:
            logger.error(f"Error preparing work record for {employee}: {e}")

    if records_to_create:
        try:
            WorkRecords.objects.bulk_create(records_to_create)
            print(f"Created {len(records_to_create)} work records for {date}.")
        except Exception as e:
            logger.error(f"Failed to bulk create work records: {e}")
    else:
        print(f"No new work records to create for {date}.")


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler(timezone=pytz.timezone(settings.TIME_ZONE))

    scheduler.add_job(
        create_work_record, "interval", minutes=30, misfire_grace_time=3600 * 3
    )
    scheduler.add_job(
        create_work_record,
        "cron",
        hour=0,
        minute=30,
        misfire_grace_time=3600 * 9,
        id="create_daily_work_record",
        replace_existing=True,
    )

    # Run every Monday at 01:00 to evaluate the previous week's hybrid compliance
    scheduler.add_job(
        check_hybrid_compliance,
        "cron",
        day_of_week="mon",
        hour=1,
        minute=0,
        misfire_grace_time=3600 * 6,
        id="check_hybrid_compliance",
        replace_existing=True,
    )

    scheduler.start()
