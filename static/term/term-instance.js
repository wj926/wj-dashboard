// wj-term — xterm + WebSocket transport adapter (wj 프로토콜)
//
// wj 의 WS 프로토콜:
//   client → server: {type:"input", data:"text"} / {type:"resize", rows, cols}
//   server → client: raw text (JSON 아님)
//
// claude-notebook 의 frontend 패턴을 차용하되 transport 부분만 wj 포맷.
//
// 외부 사용법:
//   import { WjTerm } from "/static/term/term-instance.js";
//   const t = new WjTerm({ host: el, wsUrl, sid });
//   t.mount();
//   t.send("ls\r");
//   t.dispose();

export class WjTerm {
  constructor({ host, wsUrl, sid, onStatus }) {
    this.host = host;
    this.wsUrl = wsUrl;
    this.sid = sid;
    this.onStatus = onStatus || (() => {});

    this.term = null;
    this.fitAddon = null;
    this.ws = null;
    this.manualDisconnect = false;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this._lastCols = 0;
    this._lastRows = 0;
    this._ro = null;
  }

  mount() {
    if (this.term) return;
    this.term = new window.Terminal({
      fontFamily: "'D2Coding', 'SFMono-Regular', 'Fira Code', 'Consolas', 'Courier New', monospace",
      fontSize: 14,
      lineHeight: 1.2,
      theme: {
        background: "#1e1e1e", foreground: "#d4d4d4",
        cursor: "#a855f7", selectionBackground: "#264f78",
      },
      cursorBlink: true,
      scrollback: 5000,
      smoothScrollDuration: 0,
      // 사용자가 위로 스크롤한 상태에서 출력 와도 자동 bottom 안 됨.
      // 핵심 옵션 — claude CLI thinking timer 가 매 frame 화면 흔드는 문제 해결.
      scrollOnUserInput: false,
      allowProposedApi: true,
    });
    this.fitAddon = new window.FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);
    this.term.open(this.host);
    setTimeout(() => this._fit(), 100);

    // xterm onData (사용자 키 입력) → WS 송신
    this.term.onData((d) => this._send({ type: "input", data: d }));

    // ResizeObserver — cols/rows 실제 변경시만 SIGWINCH
    let roTimer = null;
    this._ro = new ResizeObserver(() => {
      if (roTimer) clearTimeout(roTimer);
      roTimer = setTimeout(() => this._fit(), 200);
    });
    this._ro.observe(this.host);

    this._connect();
  }

  _fit() {
    try {
      if (!this.host || this.host.offsetHeight < 40 || this.host.offsetWidth < 40) return;
      this.fitAddon.fit();
      const c = this.term.cols, r = this.term.rows;
      if (c < 10 || r < 3) return;
      if (c === this._lastCols && r === this._lastRows) return;
      this._lastCols = c; this._lastRows = r;
      this._send({ type: "resize", rows: r, cols: c });
    } catch (_) {}
  }

  _connect() {
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
    let url = this.wsUrl;
    if (this.sid) url += (url.includes("?") ? "&" : "?") + "sid=" + encodeURIComponent(this.sid);
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      this._setStatus("disconnected", "WS 생성 실패");
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      if (ws !== this.ws) return;
      this.reconnectAttempts = 0;
      this._clearReconnect();
      this._setStatus("connected", "● 연결됨");
      // open 직후 현재 크기 동기화
      this._lastCols = 0; this._lastRows = 0;
      this._fit();
    };

    ws.onmessage = (ev) => {
      if (ws !== this.ws) return;
      // wj 서버는 raw text 송신. JSON 파싱 시도 후 fallback.
      const data = ev.data;
      if (typeof data === "string") {
        // 일부 wj 서버는 향후 JSON envelope 가능성 있음 — 시도해서 안 되면 raw
        this.term.write(data);
      } else if (data instanceof ArrayBuffer) {
        this.term.write(new Uint8Array(data));
      }
    };

    ws.onerror = () => {
      if (ws !== this.ws) return;  // stale 소켓 무시
      this._setStatus("disconnected", "WS 에러");
    };
    ws.onclose = () => {
      if (ws !== this.ws) return;  // stale 소켓 무시 — 이전 ws.close() 가 현재 연결 상태 덮어쓰기 방지
      this._setStatus("disconnected", "● 연결 끊김");
      this.ws = null;
      this._scheduleReconnect();
    };
  }

  _send(obj) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    try { this.ws.send(JSON.stringify(obj)); return true; }
    catch (_) { return false; }
  }

  // 레거시 호환 — 기존 코드가 __ws.send(JSON.stringify({type,data})) 패턴이라 raw 송신 노출
  sendRaw(jsonString) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    try { this.ws.send(jsonString); return true; } catch (_) { return false; }
  }
  get readyState() { return this.ws ? this.ws.readyState : 3; }

  // 외부에서 (입력바 resize 등으로) 명시적 fit 트리거. 내부 ResizeObserver 와 동일 경로.
  requestFit() { this._fit(); }

  // 스크롤바/터치 모듈이 viewport DOM 접근하기 위한 getter
  getViewport() { return this.host ? this.host.querySelector(".xterm-viewport") : null; }
  getXtermRoot() { return this.host ? this.host.querySelector(".xterm") : null; }

  // 외부 API — 텍스트 송신 (claude CLI bracketed-paste 회피: text + 50ms 후 \r 별도)
  send(text) {
    if (!text) return false;
    const ok = this._send({ type: "input", data: text });
    return ok;
  }
  sendThenEnter(text) {
    if (!this.send(text)) return false;
    setTimeout(() => this._send({ type: "input", data: "\r" }), 50);
    return true;
  }

  _setStatus(kind, label) {
    try { this.onStatus(kind, label); } catch (_) {}
  }

  _scheduleReconnect() {
    if (this.manualDisconnect) return;
    this._clearReconnect();
    // 0.5s → 1s → 2s → 4s → 8s → 10s 캡
    const delay = Math.min(500 * Math.pow(2, this.reconnectAttempts), 10000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.manualDisconnect) return;
      this._setStatus("disconnected", "● 재연결 중...");
      this._connect();
    }, delay);
  }
  _clearReconnect() {
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
  }

  dispose() {
    this.manualDisconnect = true;
    this._clearReconnect();
    if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
    if (this.term) { try { this.term.dispose(); } catch (_) {} this.term = null; }
  }

  get xterm() { return this.term; }
}
