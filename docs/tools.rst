Tools
=====

Tools are LLM-callable functions assembled at startup from ``config.toml``.
Each section below shows the config key that enables the tool, its purpose,
and key configuration options.

Memory
------

Persistent key-value store for user preferences and facts.
Config: ``[tools.kv_memory]``

.. automodule:: minibot.llm.tools.user_memory
   :no-members:

HTTP Client
-----------

Fetch HTTP/HTTPS resources with optional response spillover to managed files.
Config: ``[tools.http_client]``

.. autoclass:: minibot.llm.tools.http_client.HTTPClientTool
   :no-members:

File Storage
------------

Managed file operations scoped to a root directory.
Config: ``[tools.file_storage]``

.. autoclass:: minibot.llm.tools.file_storage.FileStorageTool
   :no-members:

Grep
----

Regex and fixed-string search over managed files.
Requires ``[tools.file_storage]``.

.. autoclass:: minibot.llm.tools.grep.GrepTool
   :no-members:

Code Execution
--------------

Bash
~~~~

Run shell commands via ``/bin/bash -lc``.
Config: ``[tools.bash]``

.. autoclass:: minibot.llm.tools.bash.BashTool
   :no-members:

Python
~~~~~~

Execute Python code with configurable sandbox isolation.
Config: ``[tools.python_exec]``

.. autoclass:: minibot.llm.tools.python_exec.HostPythonExecTool
   :no-members:

Audio Transcription
-------------------

Transcribe or translate audio files using faster-whisper.
Config: ``[tools.audio_transcription]``

.. note::

   Requires the ``stt`` extra::

      poetry install --extras stt

.. autoclass:: minibot.llm.tools.audio_transcription.AudioTranscriptionTool
   :no-members:

Scheduler
---------

Create and manage scheduled prompt jobs.
Config: ``[scheduler.prompts]``

.. autoclass:: minibot.llm.tools.scheduler.SchedulePromptTool
   :no-members:

MCP Bridge
----------

Expose remote Model Context Protocol servers as LLM tools.
Config: ``[tools.mcp]``

.. note::

   Requires the ``mcp`` extra::

      poetry install --extras mcp

.. autoclass:: minibot.llm.tools.mcp_bridge.MCPToolBridge
   :no-members:

Skills
------

Discover and load agent skills at runtime.
Config: ``skills.paths``

.. autoclass:: minibot.llm.tools.skill_loader.SkillLoaderTool
   :no-members:
