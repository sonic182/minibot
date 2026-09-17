Agent Skills
============

.. meta::
   :description: Reusable instruction packs MiniBot loads on demand — the SKILL.md format, discovery order, bundled skills, and where the agent may write new ones.
   :keywords: agent skills, SKILL.md, agentskills.io, progressive disclosure, AI agent instructions, minibot skills

A skill is a directory containing a ``SKILL.md`` file: frontmatter plus Markdown instructions.
MiniBot implements the `agentskills.io <https://agentskills.io>`_ format, so skills written for
other agent tools generally work here and vice versa.

Skills exist because of **progressive disclosure**. Only each skill's name and description are
visible to the model up front; the full instructions load into context only when the model calls
``activate_skill``. That lets an instance carry dozens of skills without paying for all of them
on every turn.

Enabling
--------

.. code-block:: toml

   [tools.skills]
   enabled = true

That attaches ``list_skills`` and ``activate_skill``, and loads the skills bundled with MiniBot.
Everything below is optional tuning.

The SKILL.md format
-------------------

.. code-block:: markdown

   ---
   name: my-skill
   description: One or two sentences saying WHEN to use this skill.
   ---

   # My Skill

   Full instructions here...

.. list-table::
   :header-rows: 1

   * - Field
     - Required
     - Notes
   * - ``name``
     - yes
     - Must match the directory name. Use lowercase and hyphens for portability.
   * - ``description``
     - in practice
     - The only part of the skill the model sees before activating it.
   * - ``enabled``
     - no
     - Defaults to ``true``. A MiniBot extension; other agent tools ignore it.

Other fields in the spec — ``license``, ``metadata``, ``compatibility``, ``allowed-tools`` — are
accepted and ignored, so they are safe to keep for portability but do nothing here.

.. warning::

   **MiniBot's frontmatter parser is stricter than the spec.** It reads flat ``key: value``
   pairs and skips indented lines, so it is not real YAML.

   A block scalar silently loses the whole description:

   .. code-block:: yaml

      # Parses as the literal string "|" — the description is gone.
      description: |
        Review a pull request diff for behavioural bugs.

   Keep every value on a single line. An **empty body** is the other silent failure: the skill
   is dropped entirely, with no error the user will see.

Writing a description that triggers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The description carries the whole activation decision, so write it to answer *when should I
reach for this*, not *what is this*. Name the situations it applies to, including ones where the
user never says the domain out loud. ``description`` warns above 300 characters; the spec's hard
limit is 1024.

.. code-block:: text

   Weak:   PR review helper.
   Better: Review a pull request diff for behavioural bugs and regression risk. Use when
           asked to review a PR, a diff, or changes on a branch.

Discovery
---------

Skills load from three sources. When ``tools.skills.paths`` is empty (the default), MiniBot
scans these locations in priority order:

.. list-table::
   :header-rows: 1

   * - Priority
     - Source
     - Path
   * - 1 (highest)
     - project
     - ``tools.skills.write_path`` (default ``./.minibot/skills/``)
   * - 2
     - project
     - ``./.minibot/skills/``
   * - 3
     - project
     - ``./.agents/skills/``
   * - 4
     - user
     - ``~/.minibot/skills/``
   * - 5
     - user
     - ``~/.agents/skills/``
   * - 6 (lowest)
     - native
     - skills bundled inside the MiniBot package

When two skills share a name, the higher-priority one wins and the other is skipped with a
warning in the logs. That is the supported way to **override** a skill rather than replace its
file — write your own with the same name in a project path, and it shadows the bundled one.

Setting ``paths`` to a non-empty list **replaces** entries 2 to 5. ``write_path`` and the
bundled skills are independent of it, so configuring ``paths`` never silently drops them. Skills
kept anywhere else — ``~/.claude/skills``, say — are picked up by listing that directory in
``paths``.

Bundled skills
--------------

MiniBot ships skills inside the package so a fresh install is not empty. They are on the lowest
tier, so anything you write wins over them.

.. code-block:: toml

   [tools.skills]
   native = true                        # the whole bundled tier
   native_disabled = ["create-skill"]   # or drop individual ones

Bundled skills carry no ``enabled`` frontmatter, because nobody edits files inside an installed
package — ``native_disabled`` is the switch.

Currently bundled:

- ``create-skill`` — how to author a skill: building it from the task you just completed rather
  than from general knowledge, writing a description that triggers, and the places this parser
  is stricter than the spec.

Where the agent writes new skills
---------------------------------

``tools.skills.write_path`` is where the agent creates or imports skills, and it is added as the
highest-priority discovery path so a skill written mid-conversation is found on the next
``list_skills`` call, with no restart.

Whether the agent can actually write there depends on which file-writing tools are enabled.
``list_skills`` reports the answer as ``write_dir_access``:

.. list-table::
   :header-rows: 1

   * - Value
     - Meaning
   * - ``filesystem``
     - The directory is inside ``[tools.file_storage] root_dir``, or ``allow_outside_root`` is
       on. ``write_dir_filesystem_path`` carries the path in the form that tool expects.
   * - ``bash``
     - Out of the ``filesystem`` tool's reach, but ``[tools.bash]`` is enabled.
   * - ``unavailable``
     - Neither applies. The agent reports this instead of attempting a write that would fail.

With the defaults in ``config.example.toml`` both writers are off, so skills are read-only. The
simplest writable setup is to enable ``[tools.file_storage]`` and point ``write_path`` inside its
root:

.. code-block:: toml

   [tools.file_storage]
   enabled = true
   root_dir = "./data/files"

   [tools.skills]
   enabled = true
   write_path = "./data/files/skills"

The tools
---------

``list_skills``
~~~~~~~~~~~~~~~

Returns up to 8 skills, ranked against an optional ``query``: exact name match, then name
prefix, then substring in the name, then substring in the description, then fuzzy similarity.
The response also carries ``write_dir``, ``write_dir_access``, ``write_dir_filesystem_path``,
``discovery_paths``, and a ``source`` of ``project``, ``user`` or ``native`` per match.

``activate_skill``
~~~~~~~~~~~~~~~~~~

Takes the exact name returned by ``list_skills`` and returns the full instructions, the skill's
directory, its source, and a list of the other files in that directory — so a skill can point at
``references/`` material the model reads only when it needs it.

The prompt catalog
~~~~~~~~~~~~~~~~~~

``tools.skills.preload_catalog`` (default ``true``) embeds a snapshot of skill names and
descriptions in the system prompt. With it off, the model is only told that ``list_skills``
exists, so it cannot tell whether a relevant skill is available — and delegating to a specialist,
whose roster *is* in the prompt, tends to win by default. ``list_skills`` still reads live from
disk either way.

Hot reload
----------

Skill files are re-read automatically. The registry fingerprints each ``SKILL.md`` by
modification time and size, so adding, editing or deleting one takes effect on the next
``list_skills`` call — no restart.

This is the opposite of prompt files under ``prompts/``, which are cached for the life of the
process, and of specialist definitions in ``agents/*.md``, which are read once at startup.

Configuration reference
-----------------------

.. code-block:: toml

   [tools.skills]
   enabled = true
   # Embed a names/descriptions snapshot in the system prompt.
   preload_catalog = true
   # Replaces the project and user discovery paths; leave empty for the defaults.
   paths = []
   # Skills bundled inside the package, independent of `paths`.
   native = true
   native_disabled = []
   # Where the agent creates or imports skills; also the top-priority discovery path.
   write_path = "./.minibot/skills"

See :doc:`config` for the full schema.

Troubleshooting
---------------

**The skill does not appear in list_skills.** Check, in this order: the body is empty; the
frontmatter block is missing or malformed; there is no ``name``; ``enabled: false`` is set; the
file is not at ``<directory>/SKILL.md``; or a higher-priority skill has the same name and is
shadowing it.

**The description shows up as** ``|``. A YAML block scalar was used — see the warning above.
Put the description on one line.

**write_dir_access is unavailable.** Enable ``[tools.bash]``, or enable
``[tools.file_storage]`` and point ``write_path`` inside its ``root_dir``.

**A bundled skill is not wanted.** Add its name to ``native_disabled``, or write your own with
the same name in a project path to override it.
