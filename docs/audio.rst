Audio Transcription
===================

.. meta::
   :description: Transcribe Telegram voice, audio, and uploaded files in Minibot with faster-whisper — setup, model selection, and GPU runtime notes.
   :keywords: speech to text AI assistant, voice transcription Telegram, faster-whisper, self-hosted STT

MiniBot can transcribe audio sent via Telegram using `faster-whisper <https://github.com/SYSTRAN/faster-whisper>`_.

Setup
-----

1. Install the ``stt`` extra::

    poetry install --extras stt

2. Ensure ``ffmpeg`` is available on the host.

3. Enable file storage and transcription in ``config.toml``:

.. code-block:: toml

   [tools.file_storage]
   enabled = true
   root_dir = "./data/files"

   [tools.audio_transcription]
   enabled = true
   model = "medium"
   device = "cpu"
   compute_type = "int8"
   beam_size = 5
   vad_filter = true

4. Send an audio file to your Telegram bot as a **document/file attachment** (``.mp3``, ``.wav``, ``.m4a``, etc.).
5. Ask the bot to transcribe it: *"transcribe this audio"*.

Notes:

- Telegram ``voice`` and ``audio`` message types are ingested automatically, as well as file/document uploads.
- If ``channels.telegram.allowed_document_mime_types`` is set, include your audio MIME types.
- In the Docker yolo profile, Whisper model assets are downloaded lazily on first use and cached under ``/app/data/.cache``.

Model Selection
---------------

``[tools.audio_transcription].model`` is a `faster-whisper
<https://github.com/SYSTRAN/faster-whisper>`_ model name, not an LLM. Valid values include ``tiny``,
``base``, ``small`` (the default), ``medium``, ``large-v3`` and ``turbo`` (``.en`` variants exist
for English-only use). Larger models are more accurate but slower.

A CPU-only host is good enough for typical use — ``model = "medium"`` with ``device = "cpu"`` and
``compute_type = "int8"`` transcribes Spanish reliably and avoids CUDA setup entirely. Use
``device = "auto"`` to prefer CUDA when available and fall back to CPU; see `GPU Runtime
Dependencies`_ below when running on GPU.

The automatic-ingest knobs default to transcribing short incoming voice/audio messages on arrival:

- ``auto_transcribe_short_incoming`` — transcribe short Telegram voice/audio attachments automatically (default: ``true``).
- ``auto_transcribe_max_duration_seconds`` — cap for automatic transcription (default: ``45``); longer files must be transcribed explicitly.

GPU Runtime Dependencies
------------------------

If STT fails with ``Library libcublas.so.12 is not found``, your CUDA runtime libraries
are missing from the loader path.

Debian / Ubuntu
~~~~~~~~~~~~~~~

.. code-block:: bash

   sudo apt update
   sudo apt install -y nvidia-driver nvidia-cuda-toolkit libcudnn9 libcudnn9-cuda-12

   echo '/usr/local/cuda/lib64' | sudo tee /etc/ld.so.conf.d/cuda.conf
   sudo ldconfig
   ldconfig -p | grep libcublas.so.12

Arch / Manjaro
~~~~~~~~~~~~~~

.. code-block:: bash

   sudo pacman -Syu cuda cudnn
   echo '/opt/cuda/lib64' | sudo tee /etc/ld.so.conf.d/cuda.conf
   sudo ldconfig
   ldconfig -p | grep libcublas.so.12

Alternative: CUDA runtime libs inside Poetry venv
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Useful when system CUDA versions do not match your Python wheel:

.. code-block:: bash

   poetry run pip install -U nvidia-cublas-cu12 nvidia-cudnn-cu12
   export SP=$(poetry run python -c "import site; print(next(p for p in site.getsitepackages() if 'site-packages' in p))")
   export LD_LIBRARY_PATH="$SP/nvidia/cublas/lib:$SP/nvidia/cudnn/lib:${LD_LIBRARY_PATH}"

Recommended STT config for GPU
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: toml

   [tools.audio_transcription]
   device = "cuda"
   compute_type = "float16"
