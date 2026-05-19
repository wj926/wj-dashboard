// wj-term — 커스텀 스크롤바 (claude-notebook 의 viewport.scrollTop 기반 패턴 포팅)
//
// 사용:
//   import { setupScrollbar } from "/static/term/term-scrollbar.js";
//   setupScrollbar({ wjTerm, barEl, thumbEl });

export function setupScrollbar({ wjTerm, barEl, thumbEl }) {
  if (!wjTerm || !barEl || !thumbEl) return () => {};
  // viewport 준비 전에도 트랙은 즉시 보이게 해서 "아예 안 보임" 착시를 방지
  barEl.classList.add("active");
  let dragging = false;
  let dragStartY = 0;
  let dragStartTop = 0;

  function getViewport() { return wjTerm.getViewport(); }

  function update() {
    barEl.classList.add("active");
    const trackH = barEl.clientHeight - 8;
    if (trackH <= 0) {
      // 트랙도 측정 불가 — 최소한 thumb 라도 노출 (절대 안 보임 방지)
      thumbEl.style.height = "40px";
      thumbEl.style.top = "4px";
      thumbEl.style.opacity = "1";
      return;
    }

    // 1차: xterm buffer 기반 (v5 가 viewport.scrollTop 안 쓰는 케이스 회피)
    const t = wjTerm.xterm;
    if (t && t.buffer && t.buffer.active) {
      const buf = t.buffer.active;
      const baseY = buf.baseY;          // 0..max scrollback offset
      const viewportY = buf.viewportY;   // 현재 top line
      const rows = t.rows;
      const totalLines = baseY + rows;
      if (totalLines > rows && baseY > 0) {
        thumbEl.style.opacity = "1";
        const thumbH = Math.max(40, (rows / totalLines) * trackH);
        const maxThumbTop = trackH - thumbH;
        const ratio = viewportY / baseY;
        thumbEl.style.height = thumbH + "px";
        thumbEl.style.top = (4 + ratio * maxThumbTop) + "px";
        return;
      }
    }

    // 2차: viewport.scrollTop 기반 (claude-notebook 패턴)
    const vp = getViewport();
    if (vp) {
      const { scrollHeight, clientHeight, scrollTop } = vp;
      if (scrollHeight > clientHeight) {
        thumbEl.style.opacity = "1";
        const thumbH = Math.max(40, (clientHeight / scrollHeight) * trackH);
        const maxThumbTop = trackH - thumbH;
        const ratio = scrollTop / (scrollHeight - clientHeight);
        thumbEl.style.height = thumbH + "px";
        thumbEl.style.top = (4 + ratio * maxThumbTop) + "px";
        return;
      }
    }

    // 3차: scrollback 없음 — thumb 가 트랙 전체. 어두운 배경 위에서도 보이게 opacity 0.6
    thumbEl.style.height = trackH + "px";
    thumbEl.style.top = "4px";
    thumbEl.style.opacity = "0.6";
  }

  function onDragStart(e) {
    e.preventDefault();
    dragging = true;
    thumbEl.classList.add("dragging");
    const y = e.touches ? e.touches[0].clientY : e.clientY;
    dragStartY = y;
    dragStartTop = parseFloat(thumbEl.style.top) || 0;
  }
  function onDragMove(e) {
    if (!dragging) return;
    e.preventDefault();
    const y = e.touches ? e.touches[0].clientY : e.clientY;
    const delta = y - dragStartY;
    const vp = getViewport();
    if (!vp) return;
    const trackH = barEl.clientHeight - 8;
    const thumbH = thumbEl.clientHeight;
    const maxThumbTop = trackH - thumbH;
    const rawTop = dragStartTop + delta - 4;
    const newTop = Math.max(0, Math.min(maxThumbTop, rawTop));
    const ratio = maxThumbTop > 0 ? newTop / maxThumbTop : 0;
    vp.scrollTop = ratio * (vp.scrollHeight - vp.clientHeight);
  }
  function onDragEnd() {
    if (!dragging) return;
    dragging = false;
    thumbEl.classList.remove("dragging");
  }

  thumbEl.addEventListener("mousedown", onDragStart);
  thumbEl.addEventListener("touchstart", onDragStart, { passive: false });
  document.addEventListener("mousemove", onDragMove);
  document.addEventListener("touchmove", onDragMove, { passive: false });
  document.addEventListener("mouseup", onDragEnd);
  document.addEventListener("touchend", onDragEnd);

  // 트랙 클릭 → 점프
  barEl.addEventListener("click", (e) => {
    if (e.target === thumbEl) return;
    const vp = getViewport();
    if (!vp) return;
    const rect = barEl.getBoundingClientRect();
    const ratio = (e.clientY - rect.top) / rect.height;
    vp.scrollTop = ratio * (vp.scrollHeight - vp.clientHeight);
  });

  // viewport 가 모바일/탭 복귀 직후 늦게 생기는 케이스 대비 — 지연 바인딩
  let boundVp = null;
  function bindViewportScroll() {
    const vp = getViewport();
    if (!vp || vp === boundVp) return;
    if (boundVp) boundVp.removeEventListener("scroll", update);
    vp.addEventListener("scroll", update);
    boundVp = vp;
  }
  bindViewportScroll();

  // xterm 출력 시 update (write 마다)
  if (wjTerm.xterm && wjTerm.xterm.onWriteParsed) {
    wjTerm.xterm.onWriteParsed(update);
  }

  // 폴링 + 지연 바인딩 fallback (느린 디바이스 / 탭 복귀 / display:none 상태에서 측정)
  const interval = setInterval(() => { bindViewportScroll(); update(); }, 1000);
  setTimeout(() => { bindViewportScroll(); update(); }, 200);

  return function dispose() {
    clearInterval(interval);
    thumbEl.removeEventListener("mousedown", onDragStart);
    thumbEl.removeEventListener("touchstart", onDragStart);
    document.removeEventListener("mousemove", onDragMove);
    document.removeEventListener("touchmove", onDragMove);
    document.removeEventListener("mouseup", onDragEnd);
    document.removeEventListener("touchend", onDragEnd);
  };
}
