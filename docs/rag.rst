RAG & Persistent Knowledge
==========================

.. meta::
   :description: Add retrieval-augmented generation to Minibot: index documents into a local SQLite vector store or Qdrant and answer with semantically retrieved chunks.
   :keywords: RAG AI assistant, persistent knowledge, SQLite vector search, Qdrant, vector search AI assistant

MiniBot can index text documents into a vector store and retrieve semantically relevant passages
at query time using `sentence-transformers <https://www.sbert.net>`_. It can also optionally rerank
the semantic candidate set with a cross-encoder for higher precision on the final results.

Two backends are available, selected with ``[tools.rag].backend``:

- ``"sqlite"`` (default) — vectors live in a local SQLite file, no extra service. Scope filters
  (``user_id``/``agent_id``/``chat_id``/``document_id``/``filename``) run in SQL first and the
  similarity scan only touches the surviving rows, so search is exact: there is no approximate
  index whose recall could degrade under filtering.
- ``"qdrant"`` — a `Qdrant <https://qdrant.tech>`_ instance. Worth the extra service once a corpus
  grows past what an exact scan handles comfortably, since its HNSW index is approximate.

This is useful when:

- The ``http_request`` tool saves an oversized HTTP response to a temp file — the bot can
  index it with ``rag_index`` and later answer questions about the content via ``rag_search``.
- A user uploads a text document and wants to query it semantically rather than reading the
  whole file into the context window.

Setup
-----

.. note::

   PDF ingestion needs the ``rag`` extra: ``pip install "minibot[rag]"``. Torch and
   sentence-transformers are not managed by Poetry and are installed manually below.

1. **Install torch**

   Choose CPU or GPU depending on your environment:

   .. code-block:: bash

      # CPU
      pip install torch --index-url https://download.pytorch.org/whl/cpu

      # GPU (CUDA, default PyPI wheel)
      pip install torch

2. **Install sentence-transformers**

   .. code-block:: bash

      pip install sentence-transformers

   Reranking uses ``sentence-transformers.CrossEncoder`` from this same install family, so
   no separate package is needed beyond ``torch`` and ``sentence-transformers``.

3. **Start Qdrant** *(only for* ``backend = "qdrant"``\ *)*

   The service is defined — commented out — in ``docker-compose.yml``. Uncomment it, then:

   .. code-block:: bash

      docker compose up minibot-qdrant

   Or point ``[tools.rag].qdrant_url`` at any Qdrant instance you already run. With the default
   ``backend = "sqlite"`` there is nothing to start.

4. **Enable RAG in** ``config.toml``:

   .. code-block:: toml

      [tools.rag]
      enabled = true
      backend = "sqlite"
      sqlite_url = "sqlite+aiosqlite:///./data/rag.db"
      collection_name = "minibot_chunks"
      chunk_size_tokens = 96
      chunk_overlap_tokens = 20
      search_limit = 5
      truncate_result_tokens = false
      max_result_tokens = 1500

      [tools.rag.embedding]
      model = "sentence-transformers/all-MiniLM-L12-v2"
      dim = 384
      max_sequence_tokens = 128
      # truncate_dim = 256  # Matryoshka truncation — see below

      [tools.rag.rerank]
      enabled = false
      model = "cross-encoder/ms-marco-MiniLM-L2-v2"
      candidate_limit = 50
      max_results = 7

   On startup, MiniBot creates the collection automatically if it does not exist. If it already
   exists with an incompatible vector size, startup fails fast — on Qdrant this also covers
   collections from the older ``source_name`` era that lack the ``filename`` payload schema.

Usage
-----

Once enabled, the bot has access to four tools:

- **rag_index** — provide a file path plus optional ``tags`` and ``categories`` metadata.
  The bot reads the file, splits it into overlapping chunks, embeds each chunk, and upserts
  the vectors into the store. Returns the number of chunks indexed and the ``document_id`` used.

- **rag_search** — provide a natural language query. The bot embeds the query and returns
  the top-k most relevant chunks with their similarity score and source metadata. Optional
  ``filename``, ``tags``, and ``categories`` filters narrow the result set. Tag/category
  filters match any of the provided values. Scope filters are bound to the active runtime
  context; if ``user_id``, ``agent_id``, or ``chat_id`` is provided explicitly, it must match
  the current context. When reranking is
  enabled, MiniBot first pulls a larger semantic candidate set from the store, reranks it with
  a cross-encoder, then returns only the final top results. Reranked responses use ``score``
  for the rerank score and also include the store's ``semantic_score``.

- **rag_list_metadata** — list available ``tags``, ``categories``, and ``filenames`` values, with counts,
  so the bot can choose real filters before calling ``rag_search``.

- **rag_delete** — remove indexed chunks by ``document_id`` and/or scope tags when the
  data should no longer be searchable. Optional ``tags`` and ``categories`` filters are also
  supported. The tool call must include at least one explicit filter; context defaults alone
  do not trigger deletion. Scope filters are bound to the active runtime context; explicit
  scope values must match it.

Example interaction::

   you: index the file at data/files/http_responses/tmp/response-abc.txt
   bot: [calls rag_index] Indexed 24 chunks under document ID doc_a394c4126b601889.

   you: what does the document say about rate limits?
   bot: [calls rag_search] Based on the indexed document, rate limits are ...

Matryoshka embeddings
---------------------

Some models (e.g. ``BAAI/bge-m3``) support Matryoshka Representation Learning, which allows
truncating the embedding to a smaller dimension without retraining.

To use a truncated dimension:

.. code-block:: toml

   [tools.rag.embedding]
   model = "BAAI/bge-m3"
   dim = 256        # effective stored vector size
   truncate_dim = 256

``dim`` and ``truncate_dim`` must match — ``dim`` tells MiniBot what size to use when
creating the collection, and ``truncate_dim`` tells sentence-transformers to truncate
the output to that size.

Resetting the collection
------------------------

When switching embedding models (different model or different ``truncate_dim``), existing
vectors are incompatible and the collection must be recreated. MiniBot validates the expected
vector size at startup and fails if the existing collection is incompatible.

With ``backend = "sqlite"``, delete the database file named by ``sqlite_url``:

.. code-block:: bash

   rm data/rag.db

With ``backend = "qdrant"``:

.. code-block:: bash

   ./scripts/rag_clear_collection.sh            # default collection
   ./scripts/rag_clear_collection.sh my_chunks  # custom name

MiniBot recreates the collection automatically on next startup. On Qdrant this reset is also
required for older collections that were indexed before ``filename`` replaced ``source_name``.

Configuration reference
-----------------------

``[tools.rag]``

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Key
     - Default
     - Description
   * - ``enabled``
     - ``false``
     - Enable ``rag_index``, ``rag_search``, ``rag_list_metadata``, and ``rag_delete`` tools.
   * - ``backend``
     - ``"sqlite"``
     - Vector store: ``"sqlite"`` (local file, exact search, no service) or ``"qdrant"``.
   * - ``sqlite_url``
     - ``sqlite+aiosqlite:///./data/rag.db``
     - Database URL, read only when ``backend = "sqlite"``.
   * - ``echo``
     - ``false``
     - Log SQL emitted by the sqlite backend.
   * - ``qdrant_url``
     - ``http://localhost:6333``
     - Qdrant HTTP endpoint, read only when ``backend = "qdrant"``.
   * - ``collection_name``
     - ``minibot_chunks``
     - Collection used for chunk vectors.
   * - ``chunk_size_tokens``
     - ``96``
     - Embedding-token count per chunk. Must not exceed ``tools.rag.embedding.max_sequence_tokens``.
   * - ``chunk_overlap_tokens``
     - ``20``
     - Embedding-token overlap between consecutive chunks. Must be less than ``chunk_size_tokens``.
   * - ``search_limit``
     - ``5``
     - Default final number of results returned by ``rag_search`` when ``limit`` is omitted.
   * - ``truncate_result_tokens``
     - ``false``
     - Truncate returned ``rag_search`` text to a token budget before sending results to the LLM.
   * - ``max_result_tokens``
     - ``1500``
     - Maximum total embedding-token budget for returned ``rag_search`` text when truncation is enabled.
   * - ``tags`` / ``categories``
     - optional
     - LLM-supplied string lists stored on each chunk; values are trimmed, lowercased,
       deduplicated, and can be used as any-match filters in search/delete.
   * - ``tools.file_storage.enabled``
     - required
     - RAG reads files through managed storage and inherits its path restrictions.

``[tools.rag.rerank]``

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Key
     - Default
     - Description
   * - ``enabled``
     - ``false``
     - Enable cross-encoder reranking after the initial semantic search.
   * - ``model``
     - ``cross-encoder/ms-marco-MiniLM-L2-v2``
     - Cross-encoder model ID loaded lazily on first reranked search.
   * - ``candidate_limit``
     - ``50``
     - Number of semantic candidates to fetch before reranking. MiniBot always fetches at least
       the requested final result count even if this value is smaller.
   * - ``max_results``
     - ``7``
     - Hard cap on final returned results when reranking is enabled. ``rag_search.limit`` and
       ``tools.rag.search_limit`` still define the requested final result count before this cap.

``[tools.rag.embedding]``

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Key
     - Default
     - Description
   * - ``model``
     - ``sentence-transformers/all-MiniLM-L12-v2``
     - Any sentence-transformers compatible model ID.
   * - ``dim``
     - ``384``
     - Full output dimension; must match the collection vector size.
   * - ``max_sequence_tokens``
     - ``128``
     - Hard max input token length for the embedding model. MiniBot fails startup if
       ``chunk_size_tokens`` exceeds this value.
   * - ``truncate_dim``
     - ``null``
     - Matryoshka truncation size. When set, must equal ``dim``.
