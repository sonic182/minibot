import Alpine from "./vendor/alpine-3.15.2.module.esm.js";

const chatElement = document.querySelector(".chat");
const socketToken = chatElement?.dataset.socketToken ?? "";
const capabilities = JSON.parse(chatElement?.dataset.chatCapabilities || "{}");
const CHUNK_SIZE = 262_144;
const sendsOnEnter = globalThis.matchMedia?.("(pointer: fine)").matches ?? true;
const COPY_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`;
const CHECK_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`;

window.webChat = () => ({
  messages: [],
  draft: "",
  atBottom: true,
  busy: false,
  stopping: false,
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
  beforeId: null,
  loadingHistory: false,

  init() {
    this.connect();
    this.$watch("draft", () => this.$nextTick(() => this.resizeComposer()));
    window.addEventListener("resize", () => this.resizeComposer());
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
      this.rejectWaiters("Connection lost.");
      for (const attachment of this.attachments) {
        attachment.uploaded = false;
      }
      window.setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10_000);
    });
    this.socket.addEventListener("error", () => {
      this.error = "Connection lost; retrying…";
    });
  },

  handle(event) {
    if (event.kind === "history_page") {
      this.receiveHistory(event);
      return;
    }
    const follow = event.role === "user" || this.atBottom;
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
    if (event.kind === "approval" && !this.findApproval(event.approval_id)) {
      this.messages.push({
        kind: "approval",
        approvalId: event.approval_id,
        toolName: event.tool_name,
        detail: event.detail,
        outcome: null,
        answering: false,
      });
    }
    if (event.kind === "approval_resolved") {
      const approval = this.findApproval(event.approval_id);
      if (approval) approval.outcome = event.outcome;
    }
    if (event.html !== undefined) {
      if (event.attachments) {
        event.attachments = event.attachments.map((attachment) => ({
          ...attachment,
          url: this.urlFor(attachment.id),
        }));
      }
      this.messages.push(event);
      if (event.role !== "user") this.stopping = false;
      if (event.role === "user" && event.attachments) {
        this.clearSent(event.attachments);
      }
    }
    if (event.busy !== undefined) {
      this.busy = event.busy;
      this.stopping = false;
    }
    if (event.error) {
      this.error = event.error;
      this.loadingHistory = false;
      this.rejectWaiters(event.error);
    }
    this.$nextTick(() => {
      if (follow) this.scrollToEnd();
    });
  },

  receiveHistory(page) {
    const list = this.$refs.messages;
    const previousHeight = list.scrollHeight;
    // The first page replaces the list, so a reconnect does not duplicate the conversation.
    this.messages = page.initial ? page.messages : [...page.messages, ...this.messages];
    this.beforeId = page.before_id;
    this.loadingHistory = false;
    this.$nextTick(() => {
      // Older pages keep the reader where they were instead of jumping by the prepended height.
      list.scrollTop = page.initial ? list.scrollHeight : list.scrollTop + list.scrollHeight - previousHeight;
      // A page that does not overflow never fires a scroll event, so ask for the next one directly.
      this.loadOlder();
    });
  },

  loadOlder() {
    if (!this.connected || !this.beforeId || this.loadingHistory || this.$refs.messages.scrollTop > 40) return;
    this.loadingHistory = true;
    this.socket.send(JSON.stringify({ kind: "history_before", before_id: this.beforeId }));
  },

  onScroll() {
    this.loadOlder();
    const list = this.$refs.messages;
    this.atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  },

  scrollToEnd() {
    this.$refs.messages.scrollTop = this.$refs.messages.scrollHeight;
    this.atBottom = true;
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

  findApproval(approvalId) {
    return this.messages.find((message) => message.kind === "approval" && message.approvalId === approvalId);
  },

  answerApproval(approval, approved) {
    if (!this.connected || approval.answering) return;
    approval.answering = true;
    this.socket.send(JSON.stringify({ kind: "approval", approval_id: approval.approvalId, approved }));
  },

  approvalOutcome(outcome) {
    return { approved: "Approved", denied: "Denied", expired: "No answer: denied" }[outcome] ?? "";
  },

  toolStatus(phase) {
    return { started: "Running", completed: "Completed", failed: "Failed" }[phase] ?? "Running";
  },

  resizeComposer() {
    const field = this.$refs.composer;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  },

  onEnter(event) {
    if (!sendsOnEnter || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    this.send();
  },

  async copyText(text) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (_) {}
    }
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  },

  async copyMessage(message, button) {
    const text = message.text ?? button.closest(".message").querySelector(".message-content").innerText;
    if (await this.copyText(text)) {
      message.copied = true;
      window.setTimeout(() => (message.copied = false), 1500);
    }
  },

  async copyCode(pre, button) {
    if (!(await this.copyText(pre.textContent.trimEnd()))) return;
    button.innerHTML = CHECK_ICON;
    window.setTimeout(() => (button.innerHTML = COPY_ICON), 1500);
  },

  decorateCodeBlocks(root) {
    this.$nextTick(() => {
      for (const pre of root.querySelectorAll("pre")) {
        if (pre.querySelector(".code-copy")) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "code-copy";
        button.setAttribute("aria-label", "Copy code");
        button.innerHTML = COPY_ICON;
        button.addEventListener("click", () => this.copyCode(pre, button));
        pre.append(button);
      }
    });
  },

  canSend() {
    return this.connected && !this.uploading && (this.draft.trim() || this.attachments.length);
  },

  canStop() {
    return this.busy && !this.draft.trim() && !this.attachments.length;
  },

  stop() {
    if (!this.connected || !this.canStop() || this.stopping) return;
    this.stopping = true;
    this.socket.send(JSON.stringify({ kind: "stop" }));
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
      const enabled = kind === "image" ? this.capabilities.images_enabled : this.capabilities.audio_enabled;
      if (!kind || !enabled) {
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
      const total = this.attachments.reduce((sum, entry) => sum + entry.file.size, 0) + file.size;
      if (total > this.capabilities.max_total_bytes) {
        this.error = "Attachments exceed the total size limit.";
        break;
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
    for (const attachment of this.attachments) {
      if (identifiers.has(attachment.id)) {
        URL.revokeObjectURL(attachment.url);
      }
    }
    this.attachments = this.attachments.filter((attachment) => !identifiers.has(attachment.id));
  },

  async upload(attachment) {
    if (attachment.uploaded) {
      return;
    }
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
    attachment.uploaded = true;
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
