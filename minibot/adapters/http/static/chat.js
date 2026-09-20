import Alpine from "./vendor/alpine-3.15.2.module.esm.js";

const chatElement = document.querySelector(".chat");
const socketToken = chatElement?.dataset.socketToken ?? "";
const capabilities = JSON.parse(chatElement?.dataset.chatCapabilities || "{}");
const CHUNK_SIZE = 262_144;

window.webChat = () => ({
  messages: [],
  draft: "",
  busy: false,
  error: "",
  connected: false,
  socket: null,
  reconnectDelay: 500,
  capabilities,
  attachments: [],
  uploading: false,
  waiters: new Map(),
  recording: false,
  recorder: null,
  recordingChunks: [],
  recordingSeconds: 0,
  recordingTimer: null,

  init() {
    this.connect();
  },

  connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${scheme}://${location.host}/chat/ws`, socketToken);
    this.socket.addEventListener("open", () => {
      this.connected = true;
      this.error = "";
      this.reconnectDelay = 500;
    });
    this.socket.addEventListener("message", ({ data }) => this.handle(JSON.parse(data)));
    this.socket.addEventListener("close", () => {
      this.connected = false;
      window.setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10_000);
    });
    this.socket.addEventListener("error", () => {
      this.error = "Connection lost; retrying…";
    });
  },

  handle(event) {
    if (event.kind === "upload_ready") {
      this.resolveWaiter(`ready:${event.upload_id}`, event);
    }
    if (event.kind === "upload_complete") {
      this.resolveWaiter(`complete:${event.attachment.id}`, event.attachment);
    }
    if (event.kind === "tool") {
      const turn = this.toolTurn(event.turn_id);
      const tool = turn.tools.find((entry) => entry.callId === event.call_id);
      if (tool) tool.phase = event.phase;
      else turn.tools.push({ callId: event.call_id, name: event.tool_name, phase: event.phase });
    }
    if (event.html !== undefined) {
      if (event.attachments) {
        event.attachments = event.attachments.map((attachment) => ({
          ...attachment,
          url: this.urlFor(attachment.id),
        }));
      }
      this.messages.push(event);
      if (event.role === "user" && event.attachments) {
        this.clearSent(event.attachments);
      }
    }
    if (event.busy !== undefined) this.busy = event.busy;
    if (event.error) {
      this.error = event.error;
      this.rejectWaiters(event.error);
    }
    this.$nextTick(() => {
      this.$refs.messages.scrollTop = this.$refs.messages.scrollHeight;
    });
  },

  resolveWaiter(key, value) {
    const waiter = this.waiters.get(key);
    if (waiter) {
      this.waiters.delete(key);
      waiter.resolve(value);
    }
  },

  rejectWaiters(reason) {
    for (const waiter of this.waiters.values()) {
      waiter.reject(new Error(reason));
    }
    this.waiters.clear();
  },

  waitFor(key) {
    return new Promise((resolve, reject) => this.waiters.set(key, { resolve, reject }));
  },

  toolTurn(turnId) {
    let turn = this.messages.find((message) => message.kind === "tools" && message.turnId === turnId);
    if (!turn) {
      turn = { kind: "tools", turnId, tools: [] };
      this.messages.push(turn);
    }
    return turn;
  },

  toolStatus(phase) {
    return { started: "Running", completed: "Completed", failed: "Failed" }[phase] ?? "Running";
  },

  canSend() {
    return this.connected && !this.uploading && (this.draft.trim() || this.attachments.length);
  },

  select(event) {
    this.addFiles(event.target.files);
    event.target.value = "";
  },

  drop(event) {
    this.addFiles(event.dataTransfer.files);
  },

  paste(event) {
    if (!this.capabilities.images_enabled || !event.clipboardData?.files.length) {
      return;
    }
    this.addFiles(event.clipboardData.files);
  },

  attachmentId() {
    if (globalThis.crypto?.randomUUID) {
      return globalThis.crypto.randomUUID();
    }
    const random = globalThis.crypto?.getRandomValues
      ? globalThis.crypto.getRandomValues(new Uint32Array(2)).join("")
      : Math.random().toString(36).slice(2);
    return `upload-${Date.now()}-${random}`;
  },

  addFiles(files) {
    for (const file of files) {
      const kind = file.type.startsWith("image/") ? "image" : file.type.startsWith("audio/") ? "audio" : null;
      if (!kind || !this.capabilities[`${kind}s_enabled`]) {
        this.error = "That attachment type is unavailable.";
        continue;
      }
      if (this.attachments.length >= this.capabilities.max_attachments) {
        this.error = `At most ${this.capabilities.max_attachments} attachments are allowed.`;
        break;
      }
      const limit = kind === "image" ? this.capabilities.max_image_bytes : this.capabilities.max_audio_bytes;
      if (file.size > limit) {
        this.error = `${file.name} is too large.`;
        continue;
      }
      this.attachments.push({
        id: this.attachmentId(),
        file,
        kind,
        filename: file.name || `${kind}.${kind === "image" ? "png" : "webm"}`,
        url: URL.createObjectURL(file),
      });
    }
  },

  remove(attachment) {
    URL.revokeObjectURL(attachment.url);
    this.attachments = this.attachments.filter((entry) => entry.id !== attachment.id);
  },

  urlFor(uploadId) {
    return this.attachments.find((attachment) => attachment.id === uploadId)?.url || "";
  },

  clearSent(sent) {
    const identifiers = new Set(sent.map((attachment) => attachment.id));
    this.attachments = this.attachments.filter((attachment) => !identifiers.has(attachment.id));
  },

  async upload(attachment) {
    this.socket.send(
      JSON.stringify({
        kind: "upload_start",
        upload_id: attachment.id,
        filename: attachment.filename,
        mime: attachment.file.type,
        size_bytes: attachment.file.size,
        media_kind: attachment.kind,
      })
    );
    await this.waitFor(`ready:${attachment.id}`);
    for (let offset = 0; offset < attachment.file.size; offset += CHUNK_SIZE) {
      this.socket.send(await attachment.file.slice(offset, offset + CHUNK_SIZE).arrayBuffer());
    }
    this.socket.send(JSON.stringify({ kind: "upload_complete", upload_id: attachment.id }));
    await this.waitFor(`complete:${attachment.id}`);
  },

  async send() {
    if (!this.canSend()) {
      return;
    }
    const text = this.draft.trim();
    this.uploading = true;
    this.error = "";
    try {
      for (const attachment of this.attachments) {
        await this.upload(attachment);
      }
      this.socket.send(
        JSON.stringify({
          kind: "message",
          text,
          upload_ids: this.attachments.map((attachment) => attachment.id),
        })
      );
      this.draft = "";
    } catch (error) {
      this.error = error.message || "Upload failed.";
    } finally {
      this.uploading = false;
    }
  },

  async toggleRecording() {
    if (this.recording) {
      this.recorder.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.recordingChunks = [];
      this.recorder = new MediaRecorder(stream);
      this.recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) {
          this.recordingChunks.push(event.data);
        }
      });
      this.recorder.addEventListener("stop", () => {
        stream.getTracks().forEach((track) => track.stop());
        const mime = this.recorder.mimeType || "audio/webm";
        this.addFiles([new File([new Blob(this.recordingChunks, { type: mime })], "recording.webm", { type: mime })]);
        this.recording = false;
        window.clearInterval(this.recordingTimer);
      });
      this.recordingSeconds = 0;
      this.recording = true;
      this.recordingTimer = window.setInterval(() => (this.recordingSeconds += 1), 1000);
      this.recorder.start();
    } catch (_) {
      this.error = "Microphone permission was not granted.";
    }
  },
});

Alpine.start();
