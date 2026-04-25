import datetime
import sys

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from base.backends import logger
from notifications.signals import notify


def _get_notification_sender():
    from django.contrib.auth.models import User

    return User.objects.filter(username="Horilla Bot").first()


def _notify_employee(employee, *, verb, verb_ar, verb_de, verb_es, verb_fr, icon):
    sender = _get_notification_sender()
    recipient = getattr(employee, "employee_user_id", None)
    if sender is None or recipient is None:
        return False

    notify.send(
        sender,
        recipient=recipient,
        verb=verb,
        verb_ar=verb_ar,
        verb_de=verb_de,
        verb_es=verb_es,
        verb_fr=verb_fr,
        redirect=reverse("view-my-attendance"),
        icon=icon,
    )
    return True


def _get_shifted_days_for_week(violation_model, employee, week_start):
    """
    Count carry-forward day requests that were shifted into the evaluated week.
    """
    from attendance.models import AttendanceShiftRequest

    request_kind = None
    if violation_model.__name__ == "HybridAttendanceViolation":
        request_kind = AttendanceShiftRequest.RequestKind.HYBRID
    elif violation_model.__name__ == "AttendancePolicyViolation":
        request_kind = AttendanceShiftRequest.RequestKind.POLICY

    filters = {
        "employee_id": employee,
        "canceled": False,
        "to_week_start_date": week_start,
    }
    if request_kind:
        filters["request_kind"] = request_kind

    return AttendanceShiftRequest.objects.filter(
        **filters
    ).count()


def notify_missed_attendance_day():
    """
    Notify employees once when the previous working day still has no
    attendance record attached to its work record.
    """
    from attendance.models import WorkRecords
    from base.methods import is_company_leave, is_holiday

    target_date = timezone.localdate() - datetime.timedelta(days=1)
    target_day = target_date.strftime("%A").lower()

    if is_holiday(target_date) or is_company_leave(target_date):
        return

    missed_records = (
        WorkRecords.objects.filter(
            date=target_date,
            work_record_type="DFT",
            missed_attendance_notified_at__isnull=True,
            employee_id__is_active=True,
            is_attendance_record=False,
            is_leave_record=False,
            employee_id__employee_work_info__shift_id__employeeshiftschedule__day__day=target_day,
        )
        .filter(
            Q(employee_id__employee_work_info__date_joining__isnull=True)
            | Q(employee_id__employee_work_info__date_joining__lte=target_date)
        )
        .select_related("employee_id__employee_user_id")
        .distinct()
    )

    notification_time = timezone.now()
    records_to_update = []
    for work_record in missed_records:
        employee = work_record.employee_id
        was_sent = _notify_employee(
            employee,
            verb=(
                f"You missed your attendance for {target_date}. "
                "Please submit or correct it if needed."
            ),
            verb_ar=(
                f"لقد فاتك تسجيل الحضور ليوم {target_date}. "
                "يرجى تقديمه أو تصحيحه إذا لزم الأمر."
            ),
            verb_de=(
                f"Sie haben Ihre Anwesenheit fuer den {target_date} verpasst. "
                "Bitte reichen Sie sie bei Bedarf nach oder korrigieren Sie sie."
            ),
            verb_es=(
                f"Has omitido tu asistencia del {target_date}. "
                "Enviala o corrigela si es necesario."
            ),
            verb_fr=(
                f"Vous avez manque votre pointage du {target_date}. "
                "Veuillez le soumettre ou le corriger si necessaire."
            ),
            icon="calendar-clear",
        )
        if was_sent:
            work_record.missed_attendance_notified_at = notification_time
            records_to_update.append(work_record)

    if records_to_update:
        WorkRecords.objects.bulk_update(
            records_to_update, ["missed_attendance_notified_at"]
        )


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
        employee_work_info__attendance_policy_id__isnull=True,
        is_active=True,
    ).select_related("employee_work_info__shift_id")

    for employee in employees:
        shift = employee.employee_work_info.shift_id
        if not shift or not shift.is_hybrid or not shift.office_days_per_week:
            continue
        shifted_days = _get_shifted_days_for_week(
            HybridAttendanceViolation, employee, week_start
        )
        required_days = shift.office_days_per_week + shifted_days

        office_days = Attendance.objects.filter(
            employee_id=employee,
            attendance_date__gte=week_start,
            attendance_date__lte=week_end,
            attendance_location="office",
        ).count()

        if office_days < required_days:
            violation, created = HybridAttendanceViolation.objects.get_or_create(
                employee_id=employee,
                week_start_date=week_start,
                defaults={
                    "shift_id": shift,
                    "required_office_days": required_days,
                    "actual_office_days": office_days,
                },
            )
            if not created:
                violation.shift_id = shift
                violation.required_office_days = required_days
                violation.actual_office_days = office_days
                violation.save(
                    update_fields=[
                        "shift_id",
                        "required_office_days",
                        "actual_office_days",
                    ]
                )
            if created or not violation.employee_notified_at:
                was_sent = _notify_employee(
                    employee,
                    verb=(
                        "You missed your weekly office attendance target for the "
                        f"week starting {week_start} "
                        f"({office_days}/{required_days} office days)."
                    ),
                    verb_ar=(
                        "لقد لم تحقق هدف الحضور المكتبي الاسبوعي للاسبوع الذي يبدأ في "
                        f"{week_start} ({office_days}/{required_days} ايام مكتبية)."
                    ),
                    verb_de=(
                        "Sie haben Ihr woechentliches Bueroanwesenheitsziel fuer die "
                        f"Woche ab dem {week_start} verfehlt "
                        f"({office_days}/{required_days} Buerotage)."
                    ),
                    verb_es=(
                        "No alcanzaste tu objetivo semanal de asistencia en oficina "
                        f"para la semana que empieza el {week_start} "
                        f"({office_days}/{required_days} dias de oficina)."
                    ),
                    verb_fr=(
                        "Vous n'avez pas atteint votre objectif hebdomadaire de "
                        f"presence au bureau pour la semaine commencant le {week_start} "
                        f"({office_days}/{required_days} jours au bureau)."
                    ),
                    icon="alert-circle",
                )
                if was_sent:
                    violation.employee_notified_at = timezone.now()
                    violation.save(update_fields=["employee_notified_at"])
        else:
            # If a previously created violation now shows compliance (manual
            # attendance corrections), update the actual count.
            HybridAttendanceViolation.objects.filter(
                employee_id=employee,
                week_start_date=week_start,
            ).update(
                actual_office_days=office_days,
                required_office_days=required_days,
            )


def check_policy_compliance():
    """
    Run every Monday to evaluate the previous week's attendance policy compliance
    for all employees who have an active attendance policy assigned.
    Creates an AttendancePolicyViolation record for every employee who did not
    meet their required office days.
    """
    from attendance.models import Attendance, AttendancePolicyViolation
    from employee.models import Employee

    today = datetime.date.today()
    days_since_monday = today.weekday()
    week_start = today - datetime.timedelta(days=days_since_monday + 7)
    week_end = week_start + datetime.timedelta(days=6)

    employees = Employee.objects.filter(
        employee_work_info__attendance_policy_id__isnull=False,
        employee_work_info__attendance_policy_id__is_active=True,
        is_active=True,
    ).select_related("employee_work_info__attendance_policy_id")

    for employee in employees:
        policy = employee.employee_work_info.attendance_policy_id
        if not policy:
            continue
        shifted_days = _get_shifted_days_for_week(
            AttendancePolicyViolation, employee, week_start
        )
        required_days = policy.required_days_per_week + shifted_days

        office_days = Attendance.objects.filter(
            employee_id=employee,
            attendance_date__gte=week_start,
            attendance_date__lte=week_end,
            attendance_location="office",
        ).count()

        if office_days < required_days:
            violation, created = AttendancePolicyViolation.objects.get_or_create(
                employee_id=employee,
                week_start_date=week_start,
                defaults={
                    "policy_id": policy,
                    "required_days": required_days,
                    "actual_days": office_days,
                },
            )
            if not created:
                violation.policy_id = policy
                violation.required_days = required_days
                violation.actual_days = office_days
                violation.save(
                    update_fields=["policy_id", "required_days", "actual_days"]
                )
            if created or not violation.employee_notified_at:
                was_sent = _notify_employee(
                    employee,
                    verb=(
                        "You missed your weekly attendance policy target for the "
                        f"week starting {week_start} "
                        f"({office_days}/{required_days} office days)."
                    ),
                    verb_ar=(
                        "لقد لم تحقق هدف سياسة الحضور الاسبوعية للاسبوع الذي يبدأ في "
                        f"{week_start} ({office_days}/{required_days} ايام مكتبية)."
                    ),
                    verb_de=(
                        "Sie haben Ihr woechentliches Anwesenheitsrichtlinienziel fuer "
                        f"die Woche ab dem {week_start} verfehlt "
                        f"({office_days}/{required_days} Buerotage)."
                    ),
                    verb_es=(
                        "No alcanzaste tu objetivo semanal de la politica de asistencia "
                        f"para la semana que empieza el {week_start} "
                        f"({office_days}/{required_days} dias de oficina)."
                    ),
                    verb_fr=(
                        "Vous n'avez pas atteint votre objectif hebdomadaire de "
                        f"politique de presence pour la semaine commencant le {week_start} "
                        f"({office_days}/{required_days} jours au bureau)."
                    ),
                    icon="alert-circle",
                )
                if was_sent:
                    violation.employee_notified_at = timezone.now()
                    violation.save(update_fields=["employee_notified_at"])
        else:
            AttendancePolicyViolation.objects.filter(
                employee_id=employee,
                week_start_date=week_start,
            ).update(actual_days=office_days, required_days=required_days)


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
    scheduler.add_job(
        notify_missed_attendance_day,
        "cron",
        hour=0,
        minute=40,
        misfire_grace_time=3600 * 6,
        id="notify_missed_attendance_day",
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

    # Run every Monday at 01:05 to evaluate the previous week's policy compliance
    scheduler.add_job(
        check_policy_compliance,
        "cron",
        day_of_week="mon",
        hour=1,
        minute=5,
        misfire_grace_time=3600 * 6,
        id="check_policy_compliance",
        replace_existing=True,
    )

    scheduler.start()
