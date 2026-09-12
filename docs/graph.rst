Relation graph
==============

The optional ``graph`` tool records typed relationships between entities. It is useful for
questions about connections — for example, who works on a project, which technology it uses,
or what a task depends on — where a normal note or a retrieved document may not contain the
complete answer in one place.

It is not a replacement for the :doc:`tools` ``memory`` tool. Store notes, dates, amounts, and
facts about one entity in memory. Store only relationships with the graph. The model receives
instructions to keep the same fact out of both stores.

Enable it
---------

The graph is a built-in extension, but is deliberately opt-in. Install MiniBot with the ``graph``
extra, then enable its extension in ``config.toml``:

.. code-block:: console

   poetry install --extras graph

.. code-block:: toml

   [extensions]
   modules = ["minibot.extensions.tools.graph"]

   [extensions.config."minibot.extensions.tools.graph"]
   sqlite_url = "sqlite+aiosqlite:///./data/graph.db"

``sqlite_url`` is optional; it defaults to ``sqlite+aiosqlite:///./data/graph.db``. Set
``echo = true`` in the extension configuration only when SQLAlchemy query logging is useful for
debugging. As with every extension, MiniBot stops at startup if it cannot import or register it.

Configuring it as an extension, rather than bundling it, also makes the tool available to spawned
task workers.

Data model and scope
--------------------

Each graph edge has a source node, relation, target node, optional attributes, and validity dates.
The store is scoped to the current owner and graph namespace, so one user's edges cannot appear in
another user's result. The namespace defaults to ``memory``; use another ``graph`` value only for
a genuinely separate domain, not to split one user's personal graph.

Nodes are typed identifiers in ``type:id`` form, such as ``person:alex``,
``project:website``, or ``tech:python``. Prefer lowercase ASCII identifiers with underscores between
words. Node and relation identifiers are normalized when written, but callers should search first
and reuse an existing identifier rather than introduce a near-duplicate. Relations are lowercase
verb phrases read from source to target, such as ``works_on``, ``uses``, ``depends_on``, or
``prefers``.

Operations
----------

``graph`` is an action-based tool. Its available actions are:

``link``
   Create or update a live edge. Requires ``source``, ``rel``, and ``target``. An optional
   ``attrs`` value is a JSON object string with additional edge fields. Repeating the same triple
   updates it instead of creating a duplicate.

``unlink``
   Close a live edge that is no longer true. Requires ``source``, ``rel``, and ``target``. The
   edge is retained as history rather than deleted.

``merge``
   Merge a duplicate node into its canonical identifier. ``source`` is the incorrect identifier;
   ``target`` is the identifier to retain. All edges mentioning the source are rewritten.

``neighbors``
   Expand relationships around ``node``. ``direction`` may be ``out`` (the default), ``in``, or
   ``both``; ``depth`` defaults to 1 and is capped at 5. ``rel`` filters traversal to one relation.
   ``limit`` defaults to 50, ``max_nodes`` defaults to 100, and ``history = true`` adds closed
   edges. A response with ``truncated = true`` reached the node limit.

``path``
   Find the shortest connection between ``source`` and ``target``. It follows relationships in
   either direction, so a useful connection can be found even if no edge points directly from the
   source to the target. ``max_depth`` defaults to 4 and is capped at 8.

``search``
   Find edges and node identifiers from a text fragment matched against source, relation, and
   target. It requires ``query`` and defaults to 25 results. Use it before adding or traversing an
   entity when its canonical identifier is unknown.

All actions accept the optional ``graph`` namespace. ``neighbors`` and ``search`` can include
closed edges with ``history = true``. Edges with ``valid_to`` are historical: present them as past
relationships, never current ones.

Examples
--------

Record that Alex works on a website project:

.. code-block:: json

   {
     "action": "link",
     "source": "person:alex",
     "rel": "works_on",
     "target": "project:website"
   }

Find everything connected to the website project within two hops:

.. code-block:: json

   {
     "action": "neighbors",
     "node": "project:website",
     "direction": "both",
     "depth": 2
   }

Explain the connection between a person and a technology:

.. code-block:: json

   {
     "action": "path",
     "source": "person:alex",
     "target": "tech:python"
   }

When a relationship changes, close the old edge and create the replacement in the same turn. This
preserves when the superseded relationship stopped being current.
