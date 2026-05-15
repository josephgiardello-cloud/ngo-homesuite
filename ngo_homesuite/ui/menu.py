from __future__ import annotations

from ngo_homesuite.auth import session
from ngo_homesuite.auth.models import admin_create_user
from ngo_homesuite.models.campaign import add_campaign, campaign_report, view_campaigns
from ngo_homesuite.models.donation import (
    allocate_donation_multi,
    record_donation,
    view_donations_by_donor,
    view_recent_donations,
    view_total_donations,
)
from ngo_homesuite.models.donor import add_donor, find_donors, tag_donor, view_donors, view_donors_by_tag
from ngo_homesuite.models.interaction import complete_followup, followups_due, log_donor_interaction, view_donor_timeline
from ngo_homesuite.models.payroll import mark_payroll_run_paid, payroll_reports, run_payroll
from ngo_homesuite.models.pledge import add_pledge, pledge_report, view_pledges
from ngo_homesuite.models.project import add_project, update_project_spent, view_projects
from ngo_homesuite.models.staff import add_staff, view_staff
from ngo_homesuite.models.volunteer import add_volunteer, view_volunteers
from ngo_homesuite.ui.reports import db_health_check, view_audit_log, view_funding_summary
from ngo_homesuite.utils.backup import backup_database
from ngo_homesuite.utils.export import export_data_csv


def main_menu() -> None:
    """Interactive CLI menu loop."""

    while True:
        print("\n--- NGO HomeSuite ---")
        if session.CURRENT_USER:
            print(
                f"Logged in as: {session.CURRENT_USER['username']} ({session.CURRENT_USER['role']})"
            )

        role = session.CURRENT_USER.get("role") if session.CURRENT_USER else None

        print("\n-- Donors & Donations (1-10) --")
        print("1. Add Donor")
        print("2. View Donors")
        print("3. Find Donors")
        if role in {"admin", "fundraiser"}:
            print("4. Tag Donor")
        if role in {"admin", "fundraiser", "viewer"}:
            print("5. View Donors By Tag")

        if role in {"admin", "fundraiser"}:
            print("6. Record Donation")
        print("7. View Total Donations")
        print("8. View Recent Donations")
        print("9. View Donations By Donor")
        if role in {"admin", "fundraiser"}:
            print("10. Allocate Donation (Multi)")

        print("\n-- Campaigns & Pledges (11-16) --")
        if role in {"admin", "fundraiser"}:
            print("11. Add Campaign")
        if role in {"admin", "fundraiser", "viewer"}:
            print("12. View Campaigns")
            print("13. Campaign Report")
        if role in {"admin", "fundraiser"}:
            print("14. Add Pledge")
        if role in {"admin", "fundraiser", "viewer"}:
            print("15. View Pledges")
            print("16. Pledge Report")

        print("\n-- Donor Interactions (17-20) --")
        if role in {"admin", "fundraiser"}:
            print("17. Log Donor Interaction")
        if role in {"admin", "fundraiser", "viewer"}:
            print("18. View Donor Timeline")
        if role in {"admin", "fundraiser"}:
            print("19. Follow-ups Due")
            print("20. Complete Follow-up")

        print("\n-- Projects & Volunteers (21-25) --")
        if role == "admin":
            print("21. Add Project")
            print("22. View Projects")
            print("23. Update Project Spent")
            print("24. Add Volunteer")
            print("25. View Volunteers")

        print("\n-- Staff & Payroll (26-30) --")
        if role == "admin":
            print("26. Add Staff")
            print("27. View Staff")
            print("28. Run Payroll")
            print("29. Payroll Reports")
            print("30. Mark Payroll Run Paid")

        print("\n-- Admin & Tools (31-36) --")
        if role == "admin":
            print("31. Create User (Admin)")
            print("32. DB Backup")
        if role in {"admin", "fundraiser"}:
            print("33. Export Data (CSV)")

        print("35. DB Health Check")
        print("36. Funding Summary")

        print("\n-- Session (37-39) --")
        print("37. Logout")
        if role == "admin":
            print("38. View Audit Log")
        print("39. Exit")

        choice = input("Choose an option: ")

        if choice == "1":
            if role in {"admin", "fundraiser"}:
                add_donor()
            else:
                print("Access denied.")
        elif choice == "2":
            view_donors()
        elif choice == "3":
            find_donors()
        elif choice == "4":
            if role in {"admin", "fundraiser"}:
                tag_donor()
            else:
                print("Access denied.")
        elif choice == "5":
            view_donors_by_tag()
        elif choice == "6":
            if role in {"admin", "fundraiser"}:
                record_donation()
            else:
                print("Access denied.")
        elif choice == "7":
            view_total_donations()
        elif choice == "8":
            view_recent_donations()
        elif choice == "9":
            view_donations_by_donor()
        elif choice == "10":
            if role in {"admin", "fundraiser"}:
                allocate_donation_multi()
            else:
                print("Access denied.")

        elif choice == "11":
            if role in {"admin", "fundraiser"}:
                add_campaign()
            else:
                print("Access denied.")
        elif choice == "12":
            view_campaigns()
        elif choice == "13":
            campaign_report()
        elif choice == "14":
            if role in {"admin", "fundraiser"}:
                add_pledge()
            else:
                print("Access denied.")
        elif choice == "15":
            view_pledges()
        elif choice == "16":
            pledge_report()

        elif choice == "17":
            if role in {"admin", "fundraiser"}:
                log_donor_interaction()
            else:
                print("Access denied.")
        elif choice == "18":
            view_donor_timeline()
        elif choice == "19":
            if role in {"admin", "fundraiser"}:
                followups_due()
            else:
                print("Access denied.")
        elif choice == "20":
            if role in {"admin", "fundraiser"}:
                complete_followup()
            else:
                print("Access denied.")

        elif choice == "21":
            if role == "admin":
                add_project()
            else:
                print("Access denied.")
        elif choice == "22":
            if role == "admin":
                view_projects()
            else:
                print("Access denied.")
        elif choice == "23":
            if role == "admin":
                update_project_spent()
            else:
                print("Access denied.")
        elif choice == "24":
            if role == "admin":
                add_volunteer()
            else:
                print("Access denied.")
        elif choice == "25":
            if role == "admin":
                view_volunteers()
            else:
                print("Access denied.")

        elif choice == "26":
            if role == "admin":
                add_staff()
            else:
                print("Access denied.")
        elif choice == "27":
            if role == "admin":
                view_staff(active_only=False)
            else:
                print("Access denied.")
        elif choice == "28":
            if role == "admin":
                run_payroll()
            else:
                print("Access denied.")
        elif choice == "29":
            if role == "admin":
                payroll_reports()
            else:
                print("Access denied.")
        elif choice == "30":
            if role == "admin":
                mark_payroll_run_paid()
            else:
                print("Access denied.")

        elif choice == "31":
            if role == "admin":
                admin_create_user()
            else:
                print("Access denied.")
        elif choice == "32":
            if role == "admin":
                backup_database()
            else:
                print("Access denied.")
        elif choice == "33":
            export_data_csv()
        elif choice == "35":
            db_health_check()
        elif choice == "36":
            view_funding_summary()
        elif choice == "37":
            session.CURRENT_USER = None
            try:
                session.CURRENT_USER = session.login()
            except session.AuthError as e:
                print(e)
                break
        elif choice == "38":
            if role == "admin":
                view_audit_log()
            else:
                print("Access denied.")
        elif choice == "39":
            break
        else:
            print("Invalid choice!")
