# Demo Recording Guide
Use this script to record a short product demo (3-5 minutes):

1. Start app locally:
	- `.venv\\Scripts\\python.exe ngo_homesuite\\main.py --web`
2. Create a temporary demo user with admin privileges in your local environment.
	- Never use shared or seeded credentials in recordings or production.
	- Rotate or delete the demo account immediately after recording.
3. Show dashboard KPIs and key navigation.
4. Open Donors, filter, and open a donor profile.
5. Record a donation and show receipt generation.
6. Open Reports and export compliance evidence.
7. Open Workflows and run one workflow via UI.
8. Open Copilot and show pending approval queue with action approval.

Screenshot capture checklist for docs/screenshots:

- dashboard-overview.png
- donors-list.png
- donor-profile.png
- donations-list.png
- reports-compliance.png
- workflows-runner.png

Suggested output filename:
- ngo-homesuite-beta-demo.mp4
