import Alpine from "./vendor/alpine-3.15.2.module.esm.js";

const chatElement = document.querySelector(".chat");
const socketToken = chatElement?.dataset.socketToken ?? "";

window.webChat = () => ({
  messages: [],
  draft: "",
  busy: false,
  error: "",
  connected: false,
  socket: null,
  reconnectDelay: 500,

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
    if (event.kind === "tool") {
      const turn = this.toolTurn(event.turn_id);
      const tool = turn.tools.find((entry) => entry.callId === event.call_id);
      if (tool) tool.phase = event.phase;
      else turn.tools.push({ callId: event.call_id, name: event.tool_name, phase: event.phase });
    }
    if (event.html !== undefined) this.messages.push(event);
    if (event.busy !== undefined) this.busy = event.busy;
    if (event.error) this.error = event.error;
    this.$nextTick(() => {
      this.$refs.messages.scrollTop = this.$refs.messages.scrollHeight;
    });
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

  send() {
    const text = this.draft.trim();
    if (!text || !this.connected) return;
    this.socket.send(JSON.stringify({ text }));
    this.draft = "";
  },
});

Alpine.start();
