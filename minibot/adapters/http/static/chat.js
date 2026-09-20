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
    if (event.html !== undefined) this.messages.push(event);
    if (event.busy !== undefined) this.busy = event.busy;
    if (event.error) this.error = event.error;
    this.$nextTick(() => {
      this.$refs.messages.scrollTop = this.$refs.messages.scrollHeight;
    });
  },

  send() {
    const text = this.draft.trim();
    if (!text || !this.connected) return;
    this.socket.send(JSON.stringify({ text }));
    this.draft = "";
  },
});

Alpine.start();
