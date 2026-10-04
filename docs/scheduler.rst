Scheduled Tasks & Automation
============================

.. meta::
   :description: Schedule one-shot, interval, and cron prompts in Minibot for Telegram automations, persisted in SQLite.
   :keywords: scheduled AI assistant, AI automation, Telegram scheduled prompts, cron AI agent

MiniBot supports one-time and recurring scheduled prompts persisted in SQLite.
Schedule by chatting naturally — no special syntax required.

Usage Examples
--------------

.. code-block:: text

   Remind me in 30 minutes to check my email.
   At 7:00 AM tomorrow, ask me for my daily priorities.
   Every day at 9 AM, remind me to send standup.
   Every weekday at 9 AM, remind me to check my calendar.
   List my active reminders.
   Cancel the standup reminder.

How It Works
------------

- **One-time**: the bot injects the prompt at the scheduled time as if you sent it.
- **Recurring**: either fixed-interval (``recurrence_type = "interval"``) or cron-based
  (``recurrence_type = "cron"`` with a standard 5-field ``recurrence_cron_expression``,
  evaluated with `croniter <https://github.com/kiorky/croniter>`_); the model picks the
  right recurrence from your phrasing, and the job re-schedules itself after each run.
- Cron expressions are evaluated in UTC. ``0 9 * * *`` fires at 09:00 UTC, so convert from your local time.
- Jobs survive restarts — they are stored in SQLite and polled on startup.
- A job is retried (up to its ``max_attempts``) only when injecting the prompt fails. If the turn it
  starts fails later, the job is not retried; the user gets a short failure reply instead.
- The minimum recurrence interval is ``scheduler.prompts.min_recurrence_interval_seconds`` (default: ``60`` seconds).

Configuration
-------------

See :class:`minibot.adapters.config.schema.ScheduledPromptsConfig` for all options.
The relevant TOML section is ``[scheduler.prompts]``.
