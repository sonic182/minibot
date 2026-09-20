Providers
=========

.. meta::
   :description: Configure MiniBot with OpenAI, Anthropic, Google, OpenRouter, ChatGPT Codex, OpenCode, xAI, Z.AI, Ollama, and compatible LLM endpoints.
   :keywords: minibot providers, OpenAI compatible API, OpenRouter, ChatGPT Codex, Ollama, LLM configuration

MiniBot sends every LLM request through the provider selected in ``[llm]``. Set its ``provider`` and
``model``, then add credentials to the matching ``[providers.<name>]`` section. Start with
``config.example.toml`` or run ``minibot configure`` to choose a provider and model interactively.

Credentials
-----------

``config.toml`` supports ``${ENVIRONMENT_VARIABLE}`` references in string values. Supply credentials
through the environment of the process running MiniBot:

.. code-block:: toml

   [providers.openai]
   api_key = "${OPENAI_API_KEY}"

An unset variable stops configuration loading with an error naming the variable and setting.
Every API-key provider needs a non-empty ``api_key``; an explicitly empty environment value or literal
empty key makes MiniBot use its local echo fallback instead of contacting the provider.

``minibot configure`` preserves references when keeping existing values and accepts new references
in credential prompts. Literal API keys are still stored as plain text, so keep files containing them
private. See :ref:`config-environment-variables` for escaping and other supported settings.

Choose a provider
-----------------

.. list-table::
   :header-rows: 1
   :widths: 24 26 50

   * - ``[llm].provider``
     - Credentials section
     - Use it for
   * - ``openai``
     - ``[providers.openai]``
     - OpenAI Chat Completions and compatible ``/v1/chat/completions`` endpoints.
   * - ``openai_responses``
     - ``[providers.openai_responses]``
     - OpenAI Responses and compatible ``/v1/responses`` endpoints.
   * - ``claude``
     - ``[providers.claude]``
     - Anthropic's native API.
   * - ``google``
     - ``[providers.google]``
     - Google's native API.
   * - ``openrouter``
     - ``[providers.openrouter]``
     - OpenRouter's model catalog and routing controls.
   * - ``chatgpt_codex``
     - ``[providers.chatgpt_codex]`` (optional)
     - A ChatGPT Codex subscription authenticated through OAuth.

The ``base_url`` and ``headers`` fields are optional for all API-key providers. Use them only for a
compatible endpoint, proxy, or provider-specific requirement. Specialist agents can select a different
provider with their ``model_provider`` frontmatter; see :doc:`agents`.

.. _providers-aliases:

Named providers
---------------

A section name above is also a client name, so ``[providers.openai_responses]`` can describe only one
Responses endpoint. To run several endpoints of the same kind side by side, name the section whatever
you like and add ``kind`` to say which client it builds:

.. code-block:: toml

   [providers.opencode_go]
   kind = "openai_responses"
   api_key = "${OPENCODE_API_KEY}"
   base_url = "https://opencode.ai/zen/go/v1"
   models = ["deepseek-v3.6", "mimo-v2.5"]

   [providers.opencode_go.headers]
   x-opencode-session = "minibot"

   [providers.zai]
   kind = "openai"
   api_key = "${ZAI_API_KEY}"
   base_url = "https://api.z.ai/api/coding/paas/v4"
   models = ["glm-5.3", "glm-5.3-flash"]

``kind`` accepts ``openai``, ``openai_responses``, ``openrouter``, ``claude``, ``google`` and
``chatgpt_codex``; leaving it unset means the section name is itself the client name. The section name
is what ``[llm].provider``, an agent's ``model_provider`` frontmatter and a runtime delegation override
reference.

``models`` is advisory: MiniBot never validates it against the endpoint, and it does not restrict what
an agent may request. It is what the main agent is shown when it asks which providers and models are
available (see :doc:`agents`), so list the ids you actually want it to pick from.

``minibot configure`` manages the client-named sections only; write aliases by hand.

OpenAI Chat Completions
-----------------------

Create an API key in the `OpenAI dashboard <https://platform.openai.com/api-keys>`_, then configure it:

.. code-block:: toml

   [llm]
   provider = "openai"
   model = "your-model-id"

   [providers.openai]
   api_key = "your-openai-api-key"

OpenAI Responses
----------------

Use ``openai_responses`` when the selected model requires the Responses API or when you want its
server-side conversation state support:

.. code-block:: toml

   [llm]
   provider = "openai_responses"
   model = "your-model-id"

   [providers.openai_responses]
   api_key = "your-openai-api-key"

Anthropic
---------

Create a key in the `Anthropic Console <https://console.anthropic.com/settings/keys>`_:

.. code-block:: toml

   [llm]
   provider = "claude"
   model = "your-model-id"

   [providers.claude]
   api_key = "your-anthropic-api-key"

Google
------

Create a key through `Google AI Studio <https://aistudio.google.com/apikey>`_:

.. code-block:: toml

   [llm]
   provider = "google"
   model = "your-model-id"

   [providers.google]
   api_key = "your-google-api-key"

OpenRouter
----------

Create a key in the `OpenRouter dashboard <https://openrouter.ai/keys>`_. MiniBot sends its app
attribution headers by default; set ``attribution_enabled = false`` only when you do not want that.

.. code-block:: toml

   [llm]
   provider = "openrouter"
   model = "provider/model"

   [providers.openrouter]
   api_key = "your-openrouter-api-key"

   [llm.openrouter]
   attribution_enabled = true
   reasoning_enabled = true

``[llm.openrouter]`` also supports a fallback model pool, plugins, and ``[llm.openrouter.provider]``
routing controls such as ``only``, ``order``, ``sort``, and ``max_price``. See :doc:`agents` for
per-specialist routing.

ChatGPT Codex subscription
--------------------------

ChatGPT Codex uses OAuth instead of an API key. Install the optional provider package, sign in, then
select a model returned by the login flow:

.. code-block:: bash

   poetry install --extras codex
   poetry run minibot codex login
   # On a headless host: poetry run minibot codex login --device-code

.. code-block:: toml

   [llm]
   provider = "chatgpt_codex"
   model = "your-codex-model-id"

Credentials default to ``~/.minibot/auth_codex.json``. To store them elsewhere, add the optional
``auth_path`` under ``[providers.chatgpt_codex]``. ``minibot configure`` can perform the same login
and model selection interactively.

Compatible endpoints
--------------------

Most compatible services use either ``openai`` for Chat Completions or ``openai_responses`` for the
Responses API. Use the API shape your endpoint documents; when unsure, start with ``openai``.

OpenCode Zen and Go
~~~~~~~~~~~~~~~~~~~

`OpenCode Zen <https://opencode.ai/zen>`_ is a pay-as-you-go compatible endpoint. OpenCode Go uses
the same API shape but requires an ``x-opencode-session`` header:

.. code-block:: toml

   [llm]
   provider = "openai"
   model = "your-model-id"

   [providers.openai]
   api_key = "your-opencode-api-key"
   base_url = "https://opencode.ai/zen/go/v1"

   [providers.openai.headers]
   x-opencode-session = "minibot"

For Zen, use ``https://opencode.ai/zen/v1`` instead. If a model is exposed only through Responses,
switch the provider and credentials table to ``openai_responses``. Keep the Go session header in that
table as well.

xAI
~~~

Use xAI's compatible Responses endpoint. MiniBot's xAI web and X-search settings apply only to this
combination:

.. code-block:: toml

   [llm]
   provider = "openai_responses"
   model = "your-model-id"

   [providers.openai_responses]
   api_key = "your-xai-api-key"
   base_url = "https://api.x.ai/v1"

   [llm.xai]
   web_search_enabled = true

Z.AI GLM Coding Plan
~~~~~~~~~~~~~~~~~~~~

MiniBot recommends Z.AI's documented Chat Completions endpoint for tool calling and streaming:

.. code-block:: toml

   [llm]
   provider = "openai"
   model = "your-model-id"

   [providers.openai]
   api_key = "your-zai-api-key"
   base_url = "https://api.z.ai/api/coding/paas/v4"

Z.AI also exposes ``https://api.z.ai/api/v1`` for Responses, but MiniBot does not treat its
``previous_response_id`` and reasoning compatibility as confirmed. Use the Chat Completions endpoint
unless you have tested the Responses variant for your model.

Ollama, vLLM, LM Studio, and other local endpoints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Start Ollama, pull a model, and configure its OpenAI-compatible endpoint:

.. code-block:: bash

   ollama serve
   ollama pull qwen3.5:35b

.. code-block:: toml

   [llm]
   provider = "openai"
   model = "qwen3.5:35b"

   [providers.openai]
   api_key = "dummy"
   base_url = "http://localhost:11434/v1"

Use ``/v1`` as the base path. MiniBot disables HTTP/2 for ``http://`` endpoints. The key must still be
non-empty, so ``"dummy"`` is appropriate for Ollama. If a compatible endpoint fails with
``openai_responses``, switch to ``openai`` first.

`vLLM <https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/>`_ serves an
OpenAI-compatible API with ``vllm serve your-model-id``. Use the same ``openai`` configuration with
``base_url = "http://localhost:8000/v1"``. Set ``api_key`` to the key supplied to vLLM, or a non-empty
placeholder when its API-key check is disabled.

`LM Studio <https://lmstudio.ai/docs/developer/openai-compat>`_ can start an OpenAI-compatible local
server from its Developer tab or with ``lms server start``. Use the same configuration with
``base_url = "http://localhost:1234/v1"`` and the loaded model's identifier. Use its configured API
token when authentication is enabled, otherwise a non-empty placeholder.

For another compatible endpoint, start from the Ollama example and replace the URL, API key, and model
ID. Use ``openai_responses`` only when the service explicitly supports the Responses API.

Troubleshooting
---------------

- The provider name in ``[llm]`` and its credentials table must match. For example,
  ``provider = "openrouter"`` reads ``[providers.openrouter]``.
- Run ``minibot configure`` to query the currently available models for API-key providers instead of
  relying on old model names in examples.
- Add custom request headers under ``[providers.<name>.headers]``. OpenCode Go is the required case:
  it rejects requests without ``x-opencode-session``.
- See :doc:`config` for every LLM and provider field, and :doc:`cli` for the console test channel and
  the Codex login command.
