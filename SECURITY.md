# Security Policy

## Supported Versions

Security fixes are prioritized for:

- The default branch
- The latest release tag

Older releases may receive fixes at maintainer discretion.

## Reporting a Vulnerability

Please do not open public issues for security vulnerabilities.

Use GitHub private vulnerability reporting:

1. Go to the repository Security tab.
2. Select Report a vulnerability.
3. Include impact, reproduction steps, affected versions, and any proof-of-concept.
4. Optional: include suggested remediation.

Target response times:

- Acknowledge receipt within 2 business days
- Provide a remediation status update within 7 business days

## Disclosure Policy

- Coordinated disclosure is preferred.
- Maintainers may request a temporary embargo until a fix is available.
- After remediation, release notes will include relevant security impact details.

## Secrets and Credentials

- Do not commit real credentials to source control.
- Rotate exposed credentials immediately.
- Use environment variables or secret files for deployment.
