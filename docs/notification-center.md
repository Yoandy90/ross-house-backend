# Centro de notificaciones

Coordinated change: backend, admin web `/admin/notificaciones`, and mobile notification routes.

## Available
- Individual/manual recipients, all active app accounts, saved manual or dynamic groups, exclusions, deduplicated recipients.
- Tenant filters use explicit account/tenant/active-contract/property links; city and housing type match the same property. Maintenance staff use explicit service-provider links and worker_type (contractor/employee).
- Administrator-only directory, recipient preview, campaign confirmation, optional schedule, cancellation before dispatch, delivery history, bilingual reusable templates.
- Durable per-person inbox, including accounts without a push token. Expo acceptance, provider receipt, failure, unknown outcome, and inbox opening are separate states.
- News publication automation is disabled initially. When enabled, first publication queues one campaign with a plain-text excerpt and article slug. Editing or republishing does not resend. Users can opt out of news.
- Mobile deep links are allowlisted and role-scoped. Generic notices open an authenticated detail page; news opens the published article.

## Scheduling and limits
The existing DISABLE_BACKGROUND_JOBS/staging policy remains authoritative. Scheduled work and Expo receipt polling require the worker to run; staging admins can explicitly run due campaigns from Historial. Immediate confirmed campaigns run as background tasks. No automatic recurrent rent or maintenance rules are added; the initial templates can be edited and scheduled individually. Existing transactional notifications remain in place.

Immediate campaigns intersect the preview with current eligibility. Scheduled campaigns reevaluate the saved audience when due; their final count may change. Manual/all audiences include eligible accounts regardless of lease state; active-contract filtering applies to tenant filter groups. Housing filters do not infer worker assignments.

Timeouts and worker crashes can happen after Expo accepts a push. Ambiguous deliveries are marked for review and never blindly retried. The initial worker is sequential and intended for the current portfolio; larger volumes need rate-aware batching. News sends require an explicit admin rule and must be tested with a small test group before enabling wider delivery.

## Validation
35 local backend tests (notification-center and mobile-delivery suites), fake MongoDB and fake Expo only. Tests cover audience identity, exclusions, deduplication, concurrency, expiry, permissions, publication idempotence, shared-device registration and ambiguous provider results. Mobile has 12 routing tests plus 10 lifecycle checks and a passing TypeScript check.

## Staging acceptance
1. Deploy coordinated backend and web preview pointing to the staging API. Install the matching mobile build.
2. Preview a test tenant, apartment group, city group, contractor and employee. Check names and push-capable counts; test overlapping groups/exclusions.
3. Confirm a message only to an explicitly selected test account. Verify inbox, push tap, full notice and opened count.
4. Schedule/cancel a test campaign; confirm due-worker behavior with the environment policy.
5. Enable news rule only for the test account. Publish a test article, verify excerpt and destination. Edit it and verify no second campaign. Disable news preference and check exclusion.
6. Confirm unauthorized users cannot read campaign details or another person's notice.

No production deployment or real campaign send is part of the local tests.
