# Simulacrum Project Review Report

This report provides an in-depth audit of the Simulacrum codebase, evaluating its functional alignment with the Product Requirements Documents (PRDs) and its technical readiness for deployment on GoDaddy Managed Servers (Passenger WSGI).

---

## 1. Functional Correctness & Verification

The core application logic has been implemented with high fidelity relative to the PRD specifications. Below is a detailed mapping of implemented systems to their PRD counterparts:

### 1.1 Resume & LinkedIn Ingestion
* **Parser Engine:** [resume_parser.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/resume_parser.py) handles PDF (via PyMuPDF) and DOCX (via python-docx) parsing correctly.
* **LinkedIn OAuth & Crawler:** [linkedin.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/linkedin.py) handles public profile crawling and normalizes results into the standard resume schema.
* **Editable parsed text:** Handled in the frontend templates to support user corrections before expertise extraction.
* **Expertise Zones:** Claude-driven extraction is cached in the `Resume.expertise_zones` JSON column, avoiding duplicate API calls unless the source text changes.

### 1.2 Stripe Monetization & Refunds
* **Dynamic pricing:** Reads `simulation_price` from `platform_settings` table at checkout in [routes.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/blueprints/simulations/routes.py#L121) and creates Stripe price objects on the fly, satisfying the dynamic requirements of the PRD.
* **Refund on failure:** [simulation.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/tasks/simulation.py#L114) correctly catches Celery/background task failures and automatically issues refunds via `issue_refund` in [stripe_service.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/stripe_service.py).
* **Webhook logic:** [routes.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/blueprints/payments/routes.py) verifies signatures, processes `payment_intent.succeeded` to trigger simulation generation and log partner commissions, and processes `charge.refunded` to cascade-refund partner commissions.

### 1.3 Client Polling & Stream Handling
* **Polling fallback:** A major deviation from the initial SSE streaming requirement, documented in [sse.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/blueprints/simulations/sse.py) and [sse.js](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/static/js/sse.js), uses client-side polling every 3 seconds to avoid holding Passenger threads open (which blocks concurrent requests in shared cPanel environments). This is a highly pragmatic and necessary optimization for GoDaddy hosting.
* **Recovery trigger:** The client calls `/api/simulations/<id>/recover` immediately on load. The server uses an atomic SQL update as a lock to prevent concurrent duplicate generation runs.

### 1.4 Growth Command Center (Layer 6)
* **Explore/Exploit logic:** Fully coded in [layer6.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/layer6.py), using a Bayesian Beta prior on yield probabilities per income stream.
* **Autonomy boundaries:** Enforces quiet hours, spend ceilings, and contact scopes. If actions exceed bounds, they are queued in the `ESC` (escalation) state in the [action queue](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/models/layer6.py#L149) and generate in-app alerts.
* **Visual network diagram:** Supported by D3.js implementation in [agent_network.js](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/static/js/agent_network.js) and [swimlane.js](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/static/js/swimlane.js).

### 1.5 Referral Partner & Advisor Program
* **Referral URL tracking:** Captures partner referral codes via session cookies and attributes client registrations.
* **Commission calculation:** Automatically calculates commission using the partner's override rate or the global platform setting, logging it in `commissions` during the Stripe webhook process.
* **Advisor access:** Enables clients to share their simulations in read-only mode with active partners. Advisors can leave custom `advisor_notes` per layer, which are stored in the database and visible to both parties.
* **Partner dual-role:** Approving a partner automatically elevates their main user account to a dual-role partner account (`User.is_partner = True`).

---

## 2. GoDaddy Shared Hosting Analysis & Fallbacks

Since standard cPanel and Passenger WSGI lack persistent background worker processes (Redis + Celery), the application implements three critical fail-safes:

1. **Eager task execution:** `CELERY_TASK_ALWAYS_EAGER = True` runs Celery tasks synchronously in the request thread if Redis is unavailable.
2. **In-process scheduler:** [scheduler.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/scheduler.py) spawns a background thread running APScheduler's `BackgroundScheduler` inside the Passenger WSGI process. It checks Layer 6 cycles every 15 minutes.
3. **cPanel Cron fallbacks:** 
   * [cron_keepalive.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/cron_keepalive.py) pings the web application every 5 minutes to prevent GoDaddy Passenger from tearing down the Python process due to inactivity.
   * [cron_generate.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/cron_generate.py) runs every minute, looking for simulations stuck in `STATUS_PROCESSING` and running their generation tasks synchronously.

---

## 3. Core Security & Auditing

* **Password Hashing:** Implemented correctly using Bcrypt with a rounds factor of 12 (dev) / 14 (prod) in [config.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/config.py#L63).
* **Token Encryption:** Uses AES-256 (via cryptography's Fernet) to encrypt LinkedIn and Stripe Connect OAuth access/refresh tokens before storing them in the database.
* **Auditing:** Execution events, manual user overrides, and admin settings updates are strictly logged in `AuditLog` and `Layer6ExecutionLog` tables.

---

## 4. Key Recommendations for Improvement

While the application is functionally complete and well-adapted to shared hosting constraints, the following modifications will significantly improve reliability, security, and maintainability in production:

### 4.1 Enforce Encryption in Production Config
* **Issue:** In [token_crypto.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/token_crypto.py#L7), if `ENCRYPTION_KEY` is not set in the environment, the functions fail-open and return/store access tokens in plaintext.
* **Why it matters:** If the server administrator forgets to configure the `ENCRYPTION_KEY` environment variable in production, sensitive user Stripe and LinkedIn tokens will be stored unencrypted in the database.
* **Recommendation:** Update `ProductionConfig` in [config.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/config.py) to raise a `RuntimeError` or `ConfigurationError` during app initialization if `ENCRYPTION_KEY` is missing or invalid.

### 4.2 Fix Potential Race Condition in `cron_generate.py`
* **Issue:** [cron_generate.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/cron_generate.py#L35) queries simulations in `STATUS_PROCESSING` and launches generation synchronously. However, there is no database-level lock or atomic update *before* the generation begins.
* **Why it matters:** If a generation takes 70 seconds to run, the cron script will run again in 60 seconds. Because the status is still `STATUS_PROCESSING` at the query moment, the second cron run will fetch the same simulation and trigger `generate_simulation_task.apply()`, resulting in parallel API calls, duplicate DB layer writes, and wasted API tokens.
* **Recommendation:** Implement an atomic update lock in `cron_generate.py` similar to the recovery mutex in `sse.py`:
  ```python
  db.session.execute(
      db.text("UPDATE simulations SET status = :new WHERE id = :sid AND status = :old"),
      {'new': 'streaming', 'sid': sim.id, 'old': 'processing'}
  )
  db.session.commit()
  ```
  Only if the update succeeds should the script proceed with generation.

### 4.3 Replace Request Thread-Spawning under Passenger
* **Issue:** In [routes.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/blueprints/simulations/routes.py#L177) and [routes.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/blueprints/layer6/routes.py#L396), the code spawns background daemon threads using Python's `threading.Thread` to execute simulation generation and Layer 6 actions when Celery workers are not available.
* **Why it matters:** WSGI servers (especially Passenger under aggressive cPanel resource limits) are designed to handle short-lived request-response cycles. Passenger frequently recycles processes under load or idle conditions. If a process recycles while a daemon thread is running a multi-step Claude generation, the thread is killed mid-execution, leaving the database state corrupt or incomplete.
* **Recommendation:** Avoid spawning long-running daemon threads during HTTP requests. Instead, simply write a "job request" entry to the database and let the scheduled minute cron [cron_generate.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/cron_generate.py) process the queue sequentially. This guarantees that process teardowns only happen between job executions, and Passenger keeps the cron process alive for its duration.

### 4.4 Establish an Automated Test Suite
* **Issue:** Aside from the SMTP smoke test in [test_email.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/test_email.py), the repository does not contain unit or integration tests.
* **Why it matters:** The system features complex integrations (Stripe, Plaid, Apollo, Cal.com), custom pricing logs, and stateful multi-layer Bayesian calculations. Updates to model definitions or route code can easily break these flows without automated verification.
* **Recommendation:** Create a `tests/` directory and implement test cases for:
  1. **Stripe webhook signatures and payments:** Mock Stripe events and verify the commission/income ledger behaves correctly.
  2. **Layer 6 Prior & Scoring calculations:** Add unit tests in [bayesian_service.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/bayesian_service.py) to assert that success/failure outcomes correctly adjust beta prior yields.
  3. **Role-based auth guards:** Test page redirection and API access for users, partners, and advisors.

### 4.5 Apollo & external API Rate Limiting Handling
* **Issue:** High-volume operations like [consulting_outreach_service.py](file:///c:/Users/jeand/OneDrive/Opts/simulacrum/app/services/consulting_outreach_service.py) integrate with third-party tools like Apollo.io.
* **Why it matters:** Apollo API keys have strict daily limits and rate limits. If the user runs multiple outreach actions, requests might fail silently or raise unhandled exceptions.
* **Recommendation:** Ensure all API services utilize the unified HTTP client from `science-skills-common` (which includes rate-limiting, retries, and exponential backoff) and track API health status metrics (`UserIntegration.health_status`, `consecutive_failures`) dynamically.
