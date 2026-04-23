RAG (Retrieval-Augmented Generation)
=====================================

MiniBot can index text documents into a `Qdrant <https://qdrant.tech>`_ vector store and
retrieve semantically relevant passages at query time using
`sentence-transformers <https://www.sbert.net>`_.

This is useful when:

- The ``http_request`` tool saves an oversized HTTP response to a temp file — the bot can
  index it with ``rag_index`` and later answer questions about the content via ``rag_search``.
- A user uploads a text document and wants to query it semantically rather than reading the
  whole file into the context window.

Setup
-----

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

3. **Start Qdrant**

   Using the pre-downloaded binary (see ``qdrant/download_bin.sh``):

   .. code-block:: bash

      ./qdrant/qdrant

   Or via Docker (service is defined in ``docker-compose.yml``):

   .. code-block:: bash

      docker compose up minibot-qdrant

4. **Enable RAG in** ``config.toml``:

   .. code-block:: toml

      [tools.rag]
      enabled = true
      qdrant_url = "http://localhost:6333"
      collection_name = "minibot_chunks"
      chunk_size = 800
      chunk_overlap = 120
      search_limit = 5

      [tools.rag.embedding]
      model = "sentence-transformers/all-MiniLM-L12-v2"
      dim = 384
      # truncate_dim = 256  # Matryoshka truncation — see below

   On startup, MiniBot creates the Qdrant collection automatically if it does not exist.

Usage
-----

Once enabled, the bot has access to three tools:

- **rag_index** — provide a file path (managed workspace or absolute). The bot reads the
  file, splits it into overlapping chunks, embeds each chunk, and upserts the vectors into
  Qdrant. Returns the number of chunks indexed and the ``document_id`` used.

- **rag_search** — provide a natural language query. The bot embeds the query and returns
  the top-k most relevant chunks with their similarity score and source metadata.

- **rag_delete** — remove indexed chunks by ``document_id`` and/or scope tags when the
  data should no longer be searchable.

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
   dim = 256        # effective vector size stored in Qdrant
   truncate_dim = 256

``dim`` and ``truncate_dim`` must match — ``dim`` tells MiniBot what size to use when
creating the Qdrant collection, and ``truncate_dim`` tells sentence-transformers to truncate
the output to that size.

Resetting the collection
------------------------

When switching embedding models (different model or different ``truncate_dim``), existing
vectors are incompatible and the collection must be recreated:

.. code-block:: bash

   ./scripts/rag_clear_collection.sh            # default collection
   ./scripts/rag_clear_collection.sh my_chunks  # custom name

MiniBot recreates the collection automatically on next startup.

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
     - Enable ``rag_index``, ``rag_search``, and ``rag_delete`` tools.
   * - ``qdrant_url``
     - ``http://localhost:6333``
     - Qdrant HTTP endpoint.
   * - ``collection_name``
     - ``minibot_chunks``
     - Qdrant collection used for chunk vectors.
   * - ``chunk_size``
     - ``800``
     - Characters per chunk. Keep below ~1000 chars for 256-token models.
   * - ``chunk_overlap``
     - ``120``
     - Character overlap between consecutive chunks.
   * - ``search_limit``
     - ``5``
     - Default number of results returned by ``rag_search``.
   * - ``tools.file_storage.enabled``
     - required
     - RAG reads files through managed storage and inherits its path restrictions.

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
     - Full output dimension; must match the Qdrant collection vector size.
   * - ``truncate_dim``
     - ``null``
     - Matryoshka truncation size. When set, must equal ``dim``.
