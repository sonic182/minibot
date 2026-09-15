Credential Vault
================

.. meta::
   :description: Store MiniBot credentials in an encrypted vault and reference them from config.toml without exposing secret values to the LLM.
   :keywords: MiniBot credential vault, encrypted secrets, secret references, API keys

MiniBot's optional credential vault stores secrets encrypted at rest and resolves them only inside
trusted configuration and adapter code. This keeps credentials out of ``config.toml`` and prevents
the LLM from requesting a secret value directly.

Install
-------

Install the optional dependency before using the vault:

.. code-block:: bash

   poetry install --extras vault

Create and edit the vault
-------------------------

The default vault path is ``secrets.vault.yml`` in the current directory:

.. code-block:: bash

   minibot vault init
   minibot vault edit
   minibot vault list

``init`` creates an empty vault. ``edit`` decrypts it into ``$EDITOR`` (then ``$VISUAL`` and
finally ``vi``), validates the edited document, and encrypts it again when the editor exits.
``list`` prints secret names only, never their values. Use a different path as a positional argument:

.. code-block:: bash

   minibot vault edit /etc/minibot/secrets.vault.yml

The document uses one ``name: value`` entry per line. Values remain strings; quote values containing
leading ``#`` or ``-``, surrounding whitespace, or newlines:

.. code-block:: yaml

   OPENAI_API_KEY: sk-example
   github_token: ghp_example
   private_key: "-----BEGIN KEY-----\nabc\n-----END KEY-----"

Configure MiniBot
-----------------

Enable the vault and point MiniBot at the file:

.. code-block:: toml

   [vault]
   enabled = true
   path = "secrets.vault.yml"

At startup MiniBot obtains the password in this order:

1. ``[vault].password_file``
2. ``MINIBOT_VAULT_PASSWORD``
3. An interactive password prompt

The interactive prompt is recommended. A password file and environment variable are suitable for
unattended deployments, but a process with the same user privileges may be able to read them.

Reference secrets
-----------------

Use ``${secret:NAME}`` in any string value in ``config.toml``. The value is resolved in memory and
the configuration file is not rewritten:

.. code-block:: toml

   [providers.openai]
   api_key = "${secret:OPENAI_API_KEY}"

   [channels.telegram]
   bot_token = "${secret:telegram}"

For an HTTP MCP server, ``auth_secret`` sends the vault value as its bearer token without exposing
it to the model:

.. code-block:: toml

   [[tools.mcp.servers]]
   name = "github"
   transport = "http"
   url = "https://api.githubcopilot.com/mcp/"
   auth_secret = "github_token"

Secret names may contain letters, numbers, underscores, dots, and hyphens, and must start with a
letter or underscore. Use ``$${secret:NAME}`` when the literal reference should remain in the
resolved configuration.

Resolution rules
----------------

- Environment references are expanded before vault references.
- ``[vault]`` settings cannot contain ``${secret:...}`` references.
- A secret reference requires ``[vault] enabled = true`` and a matching vault entry.
- Editing the vault while MiniBot is running requires a restart before the new value is used.
- The vault file is encrypted with AES-256-GCM using a key derived from the password with scrypt.
- After unlocking, MiniBot keeps the decrypted values in daemon memory; the password and derived key
  are not retained.
- The LLM can use ``list_secrets`` to see names, but there is no tool for reading secret values.

Security limits
---------------

The vault protects credentials at rest and from direct LLM tool access. It does not protect against
a compromised daemon or another same-user process that can inspect the daemon's memory. Keep the
vault file private; ``*.vault.yml`` is excluded from git and Docker build contexts by default.

See :doc:`security` for the full threat model and :doc:`config` for the complete configuration
reference. The command details are also listed in :doc:`cli`.
