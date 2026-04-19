Security & Sandboxing
=====================

MiniBot exposes a minimal tool surface by default. The most sensitive capabilities are
``python_execute``, ``bash``, and ``apply_patch`` — they can run arbitrary code or edit
host files when enabled.

Recommendations
---------------

- Disable ``tools.python_exec`` unless you need it.
- Disable ``tools.bash`` unless you need direct shell access.
- Keep ``tools.apply_patch.restrict_to_workspace = true`` unless unrestricted edits are required.
- Keep ``tools.file_storage.allow_outside_root = false`` to prevent path traversal.
- Prefer explicit sandbox isolation for untrusted code (``sandbox_mode``: ``rlimit``, ``cgroup``, ``jail``).
- Run the daemon as a non-privileged user; mount only the data directory in Docker.

Jail Mode (Firejail)
--------------------

``jail`` mode wraps the Python process with an arbitrary command prefix (e.g. ``firejail``):

.. code-block:: toml

   [tools.python_exec.jail]
   enabled = true
   command_prefix = [
     "firejail",
     "--private=/srv/minibot-sandbox",
     "--quiet",
     # "--net=none",  # restrict network access from jailed processes
   ]

Firejail + artifact export example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Create shared directory::

    mkdir -p /home/myuser/mybot/data/files/jail-shared
    chmod 700 /home/myuser/mybot/data/files/jail-shared

2. Configure Python exec:

.. code-block:: toml

   [tools.python_exec]
   sandbox_mode = "jail"
   artifacts_allow_in_jail = true
   artifacts_jail_shared_dir = "/home/myuser/mybot/data/files/jail-shared"

3. Configure Firejail wrapper:

.. code-block:: toml

   [tools.python_exec.jail]
   enabled = true
   command_prefix = [
     "firejail",
     "--quiet",
     "--noprofile",
     "--caps.drop=all",
     "--seccomp",
     "--whitelist=/home/myuser/mybot/data/files/jail-shared",
     "--read-write=/home/myuser/mybot/data/files/jail-shared",
     "--whitelist=/home/myuser/mybot/tools_venv",
   ]

Notes:

- ``artifacts_jail_shared_dir`` and the Firejail whitelist path must be identical.
- ``tools.python_exec.python_path`` (or ``venv_path``) must point to an interpreter visible inside Firejail.
- ``--noprofile`` avoids host distro defaults that may block home directory executables.
- Ensure ``firejail`` is available in the runtime image or on the host.
