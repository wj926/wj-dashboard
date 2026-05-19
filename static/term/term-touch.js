// wj-term — 모바일 touch + wheel 스크롤 캡처 (claude-notebook 패턴 포팅)
//
// 핵심:
//  - .xterm 루트에 capture 단계 + stopImmediatePropagation 으로 xterm native handler 차단
//  - touch swipe → scrollLines() 직접 호출 (viewport.scrollTop 경쟁 회피)
//  - wheel 도 capture — alternate-scroll (DECSET 1007) 변환 회피

export function setupTouchScroll({ wjTerm, dbg }) {
  if (!wjTerm) return () => {};
  let xtermRoot = wjTerm.getXtermRoot();
  // 모바일 / 느린 디바이스 — mount 직후 .xterm DOM 미생성 케이스 재시도
  if (!xtermRoot) {
    let tries = 0;
    const iv = setInterval(() => {
      tries++;
      const r = wjTerm.getXtermRoot();
      if (r) { clearInterval(iv); setupTouchScroll({ wjTerm, dbg }); }
      else if (tries > 20) clearInterval(iv);  // 4초 후 포기
    }, 200);
    return () => clearInterval(iv);
  }
  const term = wjTerm.xterm;
  // 진단 카운터 (dbg div 가 주어졌을 때만 표시)
  let cTS = 0, cTM = 0, cScr = 0;
  function dbgUpdate() {
    if (!dbg) return;
    const buf = term && term.buffer && term.buffer.active;
    const baseY = buf ? buf.baseY : "?";
    const vY = buf ? buf.viewportY : "?";
    // buffer.active === buffer.alternate ? alternate-screen 진입 (claude CLI TUI 가 켰을 가능성)
    const isAlt = term && term.buffer && (term.buffer.active === term.buffer.alternate) ? "ALT" : "norm";
    dbg.textContent = "TS:" + cTS + " TM:" + cTM + " SCR:" + cScr + " " + isAlt + " base:" + baseY + " vY:" + vY;
  }
  dbgUpdate();

  // ---------- touch ----------
  const isTouchDevice = ("ontouchstart" in window) || (navigator.maxTouchPoints > 0);
  let removeTouch = () => {};
  // tmux copy-mode 통해 스크롤 (alt-screen 안에서도 동작). term.scrollLines 는
  // tmux alt-screen 에서는 의미 없으므로 WS 메시지로 대체.
  function sendTmuxScroll(action, amount) {
    if (!wjTerm || !wjTerm.sendRaw) return;
    try {
      wjTerm.sendRaw(JSON.stringify({ type: "scroll", action, amount }));
    } catch (_) {}
  }
  if (isTouchDevice) {
    let touchActive = false;
    let lastY = 0;
    let partialLines = 0;
    let cachedRowHeight = 0;
    let pendingUp = 0, pendingDown = 0;
    let flushTimer = null;

    function getRowHeight() {
      if (cachedRowHeight > 0) return cachedRowHeight;
      const rowEl = xtermRoot.querySelector(".xterm-rows > div");
      if (rowEl) {
        const h = rowEl.getBoundingClientRect().height;
        if (h > 0) { cachedRowHeight = h; return h; }
      }
      return 20;
    }
    function flushPending() {
      flushTimer = null;
      if (pendingUp > 0) { sendTmuxScroll("up", pendingUp); pendingUp = 0; }
      if (pendingDown > 0) { sendTmuxScroll("down", pendingDown); pendingDown = 0; }
    }
    function scheduleFlush() {
      if (flushTimer != null) return;
      flushTimer = setTimeout(flushPending, 40);  // ~25 fps throttle
    }
    function onTouchStart(ev) {
      cTS++; dbgUpdate();
      if (!term || !ev.touches || ev.touches.length !== 1) return;
      touchActive = true;
      lastY = ev.touches[0].clientY;
      partialLines = 0;
      ev.stopImmediatePropagation();
    }
    function onTouchMove(ev) {
      cTM++; dbgUpdate();
      if (!touchActive || !term || !ev.touches || ev.touches.length !== 1) return;
      const y = ev.touches[0].clientY;
      const deltaY = lastY - y;
      lastY = y;
      if (deltaY === 0) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      partialLines += deltaY / getRowHeight();
      const lines = partialLines > 0 ? Math.floor(partialLines) : Math.ceil(partialLines);
      if (lines !== 0) {
        // 위로 스와이프 (손가락 위로) = 콘텐츠 위로 스크롤 (= "up" tmux). lines>0 이 그 케이스.
        if (lines > 0) pendingUp += lines;
        else pendingDown += -lines;
        scheduleFlush();
        cScr += lines; dbgUpdate();
        partialLines -= lines;
      }
    }
    function onTouchEnd() {
      touchActive = false;
      partialLines = 0;
      if (flushTimer != null) { clearTimeout(flushTimer); flushPending(); }
    }

    xtermRoot.addEventListener("touchstart", onTouchStart, { capture: true, passive: true });
    xtermRoot.addEventListener("touchmove",  onTouchMove,  { capture: true, passive: false });
    xtermRoot.addEventListener("touchend",   onTouchEnd,   { capture: true, passive: true });
    xtermRoot.addEventListener("touchcancel",onTouchEnd,   { capture: true, passive: true });
    removeTouch = () => {
      xtermRoot.removeEventListener("touchstart", onTouchStart, true);
      xtermRoot.removeEventListener("touchmove",  onTouchMove,  true);
      xtermRoot.removeEventListener("touchend",   onTouchEnd,   true);
      xtermRoot.removeEventListener("touchcancel",onTouchEnd,   true);
    };
  }

  // ---------- wheel ----------
  function onWheel(ev) {
    if (!term) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    let delta = ev.deltaY;
    if (delta === 0) return;
    if (ev.deltaMode === 1) delta *= 16;       // LINE
    else if (ev.deltaMode === 2) delta *= 100; // PAGE
    const rowHeight = 20;
    const lines = delta > 0
      ? Math.max(1, Math.round(delta / rowHeight))
      : Math.min(-1, Math.round(delta / rowHeight));
    // tmux copy-mode 스크롤 (alt-screen 안에서도 동작). 데스크탑 wheel 도 동일 경로.
    if (lines > 0) sendTmuxScroll("down", lines);
    else sendTmuxScroll("up", -lines);
  }
  xtermRoot.addEventListener("wheel", onWheel, { capture: true, passive: false });

  // viewport overflow anchor 비활성 — 출력 와도 scrollTop 자동 흔들리지 않게
  const vp = wjTerm.getViewport();
  if (vp) vp.style.overflowAnchor = "none";

  return function dispose() {
    removeTouch();
    xtermRoot.removeEventListener("wheel", onWheel, true);
  };
}
