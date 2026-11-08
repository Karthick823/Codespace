# AI Collections Employee — Pre-Production Security Hardening Checklist

**Status: NOT PRODUCTION READY.** V3.7.1/V3.8 are local prototypes. Passing tests does not certify the whole Streamlit application, host, database, identity boundary, or external provider configuration.

## P0 — Required before any external customer use

### 1. Direct database and process access
- [ ] Move from a developer-writable SQLite file to a managed database with authenticated service accounts and least-privilege roles.
- [ ] Run the application as a dedicated OS/service identity, not a personal administrator account.
- [ ] Restrict filesystem ACLs for source code, database, logs, uploads, backups, environment files, and secrets.
- [ ] Ensure app users cannot execute arbitrary Python, open a database shell, read environment variables, or access the host process.
- [ ] Separate application runtime identity from deployment/admin identity.
- [ ] Define backup encryption, retention, access, restore drills, and secure deletion.
- [ ] Add database migrations with schema versions, rollback plan, and pre-migration backup verification.

### 2. Deployment hardening
- [ ] Deploy behind a supported reverse proxy/application hosting setup with HTTPS/TLS.
- [ ] Disable debug mode and avoid exposing Streamlit directly to the public internet.
- [ ] Add network restrictions, firewall rules, rate limiting, security headers where applicable, and controlled egress.
- [ ] Pin and scan dependencies; maintain a software bill of materials and patch process.
- [ ] Use separate development, test, staging, and production environments and databases.
- [ ] Add centralized error handling that does not expose stack traces, secrets, or customer data to users.
- [ ] Define health checks, monitoring, alerting, capacity limits, incident response, and disaster recovery.

### 3. SSO and MFA
- [ ] Replace local prototype login with an approved identity provider (e.g., Microsoft Entra ID for a Microsoft 365 customer).
- [ ] Enforce MFA/conditional access through the identity provider.
- [ ] Map stable identity-provider subject IDs to internal users; never trust user-supplied role names.
- [ ] Enforce role and permission checks inside every service operation, not only in Streamlit controls.
- [ ] Add joiner/mover/leaver process, session revocation, inactive-user handling, and privileged-account review.

### 4. Session hardening
- [ ] Use a supported server-side session strategy and secure cookie/session settings for the chosen deployment.
- [ ] Enforce idle and absolute session timeouts and session rotation after authentication/privilege changes.
- [ ] Prevent session fixation and validate CSRF/origin protections for any custom endpoints.
- [ ] Avoid storing unnecessary sensitive customer information in session state or browser-visible output.
- [ ] Test logout, concurrent sessions, stale sessions, password/role changes, and access revocation.

### 5. Immutable audit evidence
- [ ] Send audit events to a separately administered append-only/WORM-capable store or equivalent tamper-resistant service.
- [ ] Hash-chain/sign events or use provider-native immutable retention controls; protect signing keys separately.
- [ ] Include actor identity, authorization context, request/correlation ID, resource/version, decision, timestamp, provider outcome, and recovery action.
- [ ] Exclude secrets, access tokens, passwords, and full email bodies from routine logs.
- [ ] Define retention, privacy, access, legal hold, export, and audit review procedures.
- [ ] Alert on audit-write failures; sensitive actions must fail closed if required audit persistence is unavailable.
- [ ] Test detection of log deletion, alteration, and gaps.

### 6. Migrate/restrict every legacy entry point
- [ ] Inventory all modules that can create/decide approvals, change task/workflow status, edit drafts, read customer data, send email, or write to the database.
- [ ] Route every state-changing operation through a single authenticated service layer.
- [ ] Remove or restrict direct calls to legacy `approval_service.py`, old agents/orchestrators, and older Streamlit app paths.
- [ ] Add negative authorization tests for each service method and role.
- [ ] Ensure a reviewer cannot approve their own request, including alternate usernames/identity mappings.
- [ ] Ensure approved draft version, recipient, body, and invoice linkage cannot change between approval and sending.
- [ ] Prevent alternate scripts, imports, and direct service calls from bypassing policy.

## P0 — Email-specific controls before live sending
- [ ] Obtain written authorization for the Microsoft 365 app registration and dedicated sender mailbox.
- [ ] Grant only the required Microsoft Graph `Mail.Send` application permission and admin consent.
- [ ] Restrict the app to a dedicated mailbox using supported Exchange mailbox-scoping controls; verify the restriction with negative tests.
- [ ] Replace client secret with certificate/federated credential where supported; store credentials in a managed secrets vault and rotate them.
- [ ] Verify recipient against an authoritative invoice/customer contact record, not only email syntax.
- [ ] Require approval of the exact draft version, recipient, subject, body, and invoice reference.
- [ ] Enforce separation of duties and restricted sender roles in the service layer.
- [ ] Add a provider reconciliation procedure for timeouts and `Delivery Unknown`; never blindly retry.
- [ ] Configure outbound domain controls, SPF/DKIM/DMARC, mailbox sending limits, and monitoring with the tenant administrator.
- [ ] Start with a test mailbox, synthetic invoices, a small allowlist, low volume, and an immediate kill switch.
- [ ] Define incident response for wrong-recipient sends, duplicate sends, credential exposure, and compromised accounts.

## P1 — Reliability, privacy, and quality
- [ ] Move workflow/task/approval/draft/send operations toward transactional consistency or a transactional outbox.
- [ ] Add concurrency and fault-injection tests, including crashes between each database write and provider response.
- [ ] Add automated integration tests against a non-production Microsoft 365 tenant with provider calls safely controlled.
- [ ] Add data minimization, encryption at rest/in transit, retention/deletion policy, and data-subject request process.
- [ ] Test prompt injection in customer replies and ensure email content cannot override system/business policy.
- [ ] Add model-output evaluation for factual accuracy, tone, legal/escalation language, and hallucinated invoice details.
- [ ] Add monitoring dashboards for approval latency, draft quality, send failures, unknown outcomes, and operator overrides.
- [ ] Complete security review/threat model, privacy review, and penetration test before general availability.

## Release gate
Do not label the system production-ready until every P0 item has an owner, evidence of completion, and approval from the responsible security/IT/business stakeholders. A passing unit-test suite is necessary but not sufficient.
