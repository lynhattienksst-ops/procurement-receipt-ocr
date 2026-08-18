# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability (such as exposed credentials, API leakage, or injection vectors), please do not open a public issue. Instead, report it privately to the maintainers.

### Security Best Practices When Deploying:

1. **Protect your `.env` and `service_account.json`**:
   - Ensure `.gitignore` is never modified to track `.env` or `service_account.json`.
   - Grant only **Least Privilege** permissions to your Google Cloud Service Account (Google Drive Viewer, Google Sheets Editor).
2. **Reverse Proxy & HTTPS**:
   - When deploying in production over public networks, configure SSL/TLS certificates via Nginx or Cloudflare.
