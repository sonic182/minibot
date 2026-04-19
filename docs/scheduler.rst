Scheduler
=========

MiniBot supports one-time and recurring scheduled prompts persisted in SQLite.
Schedule by chatting naturally — no special syntax required.

Usage Examples
--------------

.. code-block:: text

   Remind me in 30 minutes to check my email.
   At 7:00 AM tomorrow, ask me for my daily priorities.
   Every day at 9 AM, remind me to send standup.
   List my active reminders.
   Cancel the standup reminder.

How It Works
------------

- **One-time**: the bot injects the prompt at the scheduled time as if you sent it.
- **Recurring**: interval-based; the job re-schedules itself after each run.
- Jobs survive restarts — they are stored in SQLite and polled on startup.
- The minimum recurrence interval is ``scheduler.prompts.min_recurrence_interval_seconds`` (default: ``60`` seconds).

Configuration
-------------

See :class:`minibot.adapters.config.schema.ScheduledPromptsConfig` for all options.
The relevant TOML section is ``[scheduler.prompts]``.
