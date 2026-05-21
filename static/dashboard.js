// wj-dashboard — 인터랙션 전체

(function () {
  const byDay = window.__BY_DAY__ || {};
  const projects = window.__PROJECTS__ || {};
  const inbox = window.__INBOX__ || [];
  const todayISO = window.__TODAY_ISO__;

  // ============ 유틸 ============
  const $ = sel => document.querySelector(sel);
  const $$ = sel => document.querySelectorAll(sel);

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  function urgClass(u) {
    return ({ urgent: "urgent", soon: "soon", ok: "ok", none: "none" })[u] || "none";
  }

  function ddayLabel(d) {
    if (d === null || d === undefined) return "—";
    if (d === 0) return "D-0";
    if (d > 0) return "D-" + d;
    return "D+" + Math.abs(d);
  }

  function dowKr(iso) {
    return ["일", "월", "화", "수", "목", "금", "토"][new Date(iso).getDay()];
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${parseInt(m)}/${parseInt(d)}`;
  }

  function toast(msg, type) {
    const el = $("#toast");
    el.textContent = msg;
    el.style.background = type === "err" ? "#b02a37" : type === "ok" ? "#1b6e4f" : "#1f3a5f";
    el.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.style.display = "none", 2500);
  }

  async function api(path, opts) {
    try {
      const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      return j;
    } catch (e) {
      toast("✗ " + e.message, "err");
      throw e;
    }
  }

  function reloadSoon() {
    // 잠시 후 새로고침 (yaml 갱신 → polling 도 잡지만 즉시 반영하려고)
    setTimeout(() => location.reload(), 400);
  }

  // ============ 액션 ============
  async function actDone(taskId) {
    await api(`/api/task/${encodeURIComponent(taskId)}/done`, { method: "POST" });
    toast("✓ 완료", "ok");
    reloadSoon();
  }
  async function actUndo(taskId) {
    await api(`/api/task/${encodeURIComponent(taskId)}/undo`, { method: "POST" });
    toast("↶ 취소", "ok");
    reloadSoon();
  }
  async function actSnooze(taskId, days) {
    await api(`/api/task/${encodeURIComponent(taskId)}/snooze?days=${days}`, { method: "POST" });
    toast(`+${days}일 미룸`, "ok");
    reloadSoon();
  }
  // ============ 진짜 챗봇 — Claude Opus ============
  const chatHistory = [];   // [{role, content}, ...]
  let lastTask = null;       // 봇이 가장 마지막에 제안한 task

  function appendBubble(html, who) {
    const bb = $("#chat-body");
    const div = document.createElement("div");
    div.className = `bubble ${who}`;
    div.innerHTML = `<div class="bubble-content">${html}</div>`;
    bb.appendChild(div);
    bb.scrollTop = bb.scrollHeight;
    return div;
  }

  function appendThinking() {
    const bb = $("#chat-body");
    const div = document.createElement("div");
    div.className = "bubble bot thinking";
    div.id = "thinking-bubble";
    div.innerHTML = '<div class="bubble-content">생각 중…</div>';
    bb.appendChild(div);
    bb.scrollTop = bb.scrollHeight;
    return div;
  }

  function removeThinking() {
    document.getElementById("thinking-bubble")?.remove();
  }

  function renderChatEmptyState() {
    const examples = [
      "내일 오후 3시 회의 준비",
      "5/25 논문 초안 마감",
      "이번주 도서관 자료 정리",
      "다음주 월요일 세미나 발표 자료",
    ];
    $("#chat-body").innerHTML = `
      <div class="chat-empty">
        <div class="ce-title">할 일을 자연어로 적어 보세요</div>
        <div class="ce-sub">날짜·프로젝트를 자동으로 정리해 드립니다. 아래 예시를 눌러 바로 시작할 수 있어요.</div>
        <div class="ce-chips">
          ${examples.map(e => `<button type="button" class="ce-chip" data-ex="${escapeHtml(e)}">${escapeHtml(e)}</button>`).join("")}
        </div>
      </div>`;
  }

  function openChat(firstMessage) {
    // reset
    chatHistory.length = 0;
    lastTask = null;
    $("#chat-body").innerHTML = "";
    $("#chat-input").value = "";
    $("#ov-chat").classList.add("on");
    setTimeout(() => $("#chat-input").focus(), 100);
    if (firstMessage) sendChat(firstMessage);
    else renderChatEmptyState();
  }

  async function sendChat(text) {
    text = text.trim();
    if (!text) return;
    chatHistory.push({ role: "user", content: text });
    appendBubble(escapeHtml(text), "user");
    appendThinking();
    $("#chat-input").disabled = true;
    $("#chat-send").disabled = true;
    try {
      const r = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ messages: chatHistory }),
      });
      removeThinking();
      if (!r.ok) {
        appendBubble(`⚠ Claude 호출 실패: ${escapeHtml(r.error || "unknown")}`, "bot");
        return;
      }
      // 봇 응답 표시
      const reply = r.reply || "(응답 없음)";
      let html = escapeHtml(reply);
      if (r.task && !r.needs_clarification) {
        lastTask = r.task;
        const ICN_TITLE = '<svg class="icn" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
        const ICN_DATE = '<svg class="icn" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
        const ICN_FOLDER = '<svg class="icn" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
        const ICN_NOTE = '<svg class="icn sm" viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>';
        const ICN_CHECK = '<svg class="icn" viewBox="0 0 24 24" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
        const ICN_X = '<svg class="icn" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
        html += `
          <div class="task-preview">
            <div class="pv-line">${ICN_TITLE}<b>${escapeHtml(r.task.title || "(제목 없음)")}</b></div>
            <div class="pv-line">${ICN_DATE}<span>${r.task.due_at ? `${r.task.due_at} (${dowKr(r.task.due_at)}요일)` : "마감 미정"}</span></div>
            <div class="pv-line">${ICN_FOLDER}<span>${escapeHtml(projectNameById(r.task.project_id))}</span></div>
            ${r.task.note ? `<div class="pv-line">${ICN_NOTE}<span style="font-size:0.8rem;color:#6b7785;">${escapeHtml(r.task.note)}</span></div>` : ""}
          </div>
          <div class="chat-actions">
            <button class="primary" onclick="window.__confirmTask()">${ICN_CHECK} 등록</button>
            <button class="ghost" onclick="window.__cancelTask()">${ICN_X} 취소</button>
          </div>`;
      } else if (r.needs_clarification && r.questions && r.questions.length) {
        html += `<ul class="questions">${r.questions.map(q => `<li>${escapeHtml(q)}</li>`).join("")}</ul>`;
      }
      appendBubble(html, "bot");
      chatHistory.push({ role: "assistant", content: reply });
    } catch (e) {
      removeThinking();
    } finally {
      $("#chat-input").disabled = false;
      $("#chat-send").disabled = false;
      $("#chat-input").focus();
    }
  }

  function projectNameById(pid) {
    if (!pid) return "Inbox";
    const p = projects[pid];
    return p ? p.title : pid;
  }

  // 글로벌 콜백 (HTML inline onclick 에서 사용)
  window.__confirmTask = async function () {
    if (!lastTask) return;
    await api("/api/task/commit", {
      method: "POST",
      body: JSON.stringify({
        title: lastTask.title,
        due_at: lastTask.due_at || null,
        project_id: lastTask.project_id || null,
        note: lastTask.note || "",
      }),
    });
    appendBubble(`✓ 등록 완료 — <b>${escapeHtml(lastTask.title)}</b>`, "bot");
    lastTask = null;
    toast("등록됨", "ok");
    setTimeout(() => {
      $("#ov-chat").classList.remove("on");
      reloadSoon();
    }, 800);
  };

  window.__cancelTask = function () {
    lastTask = null;
    appendBubble("취소했어요. 다른 요청 있으면 말씀하세요.", "bot");
  };

  // ============ 캘린더 날짜 모달 ============
  function openDayModal(iso) {
    const tasks = byDay[iso] || [];
    const [y, m, d] = iso.split("-").map(Number);
    const dow = dowKr(iso);
    const isToday = iso === todayISO;
    $("#ov-day-title").textContent = `${m}월 ${d}일 (${dow}요일)` + (isToday ? " · 오늘" : "");
    const body = $("#ov-day-body");
    if (tasks.length === 0) {
      body.innerHTML = '<p style="color:#888;padding:20px 0;text-align:center;">이 날 등록된 일 없음.</p>';
    } else {
      body.innerHTML = `<p style="color:#6b7785;font-size:0.82rem;margin:0 0 10px;">${tasks.length}건의 task</p>` +
        tasks.map((t, i) => taskItemHtml(t, i + 1)).join("");
      bindTaskActions(body);
    }
    $("#ov-day").classList.add("on");
  }

  // ============ 프로젝트 모달 ============
  function openProjectModal(pid) {
    const p = projects[pid];
    if (!p) {
      toast("프로젝트 정보 없음", "err");
      return;
    }
    $("#ov-project-title").textContent = `🎯 ${p.title}`;
    const tags = (p.tags || []).map(t => `<span style="background:#e8f5ef;color:#1b6e4f;font-size:0.7rem;padding:1px 7px;border-radius:8px;font-weight:700;">${escapeHtml(t)}</span>`).join(" ");
    const meta = `${p.todo_count} todo · ${p.done_count} done · 진행 ${p.progress_pct}%` + (p.due_at ? ` · 마감 ${fmtDate(p.due_at)}` : "") + (p.source_thinking ? ` · 💭 thinking` : "");
    const body = $("#ov-project-body");
    if (p.tasks.length === 0) {
      body.innerHTML = `<div style="margin-bottom:12px;font-size:0.78rem;color:#6b7785;">${tags} · ${meta}</div><p style="color:#888;padding:20px 0;text-align:center;">등록된 task 없음.</p>`;
    } else {
      // todo + doing 먼저, done 마지막
      const sorted = [...p.tasks].sort((a, b) => {
        const order = { todo: 0, doing: 0, done: 2 };
        return (order[a.status] || 1) - (order[b.status] || 1);
      });
      body.innerHTML = `<div style="margin-bottom:12px;font-size:0.78rem;color:#6b7785;">${tags} · ${meta}</div>` +
        sorted.map((t, i) => taskItemHtml(t, i + 1)).join("");
      bindTaskActions(body);
    }
    $("#ov-project").classList.add("on");
  }

  // ============ task 항목 HTML — Inbox 와 통일된 체크박스 형식 ============
  // 모달 안 task 데이터 캐시 — 편집 폼 열고 닫을 때 사용
  const _taskCache = {};

  function taskItemHtml(t, _num) {
    _taskCache[t.id] = t;
    const isDone = t.status === "done";
    const dueLabel = t.due_at ? `${fmtDate(t.due_at)} · ${ddayLabel(t.d_minus)}` : "마감 미정";
    return `
      <div class="sd-item ${isDone ? "is-done" : ""}" data-task-id="${escapeHtml(t.id)}" data-row="view">
        <input type="checkbox" class="task-check sd-check" data-task-id="${escapeHtml(t.id)}" aria-label="완료 처리: ${escapeHtml(t.title)}" ${isDone ? "checked" : ""} />
        <div class="body">
          <div class="title">${escapeHtml(t.title)}</div>
          <div class="meta">
            <span class="dday ${urgClass(t.urgency)}">${dueLabel}</span>
            ${t.project ? `<span>${escapeHtml(t.project)}</span>` : ""}
          </div>
          ${t.note ? `<div class="note">${escapeHtml(t.note)}</div>` : ""}
        </div>
        <button type="button" class="edit-btn" data-task-id="${escapeHtml(t.id)}" title="수정">✎</button>
      </div>
    `;
  }

  function projectOptionsHtml(currentId) {
    // window.__PROJECTS__ 는 {id: {id,title,...}} 맵
    const opts = [`<option value="">— Inbox / 프로젝트 없음 —</option>`];
    const ids = Object.keys(projects);
    for (const pid of ids) {
      const p = projects[pid];
      const sel = pid === currentId ? " selected" : "";
      opts.push(`<option value="${escapeHtml(pid)}"${sel}>${escapeHtml(p.title || pid)}</option>`);
    }
    // 유령 project_id (active 목록에 없는데 task 가 가리키는 경우) 보존용 옵션
    if (currentId && !projects[currentId]) {
      opts.push(`<option value="${escapeHtml(currentId)}" selected>⚠ 비활성 (${escapeHtml(currentId)})</option>`);
    }
    return opts.join("");
  }

  function taskEditFormHtml(t) {
    const due = t.due_at ? String(t.due_at).slice(0, 10) : "";
    const pid = t.project_id || "";
    return `
      <div class="sd-item edit-form" data-task-id="${escapeHtml(t.id)}" data-row="edit">
        <div class="body" style="flex:1;">
          <label class="ef-row"><span class="ef-lab">제목</span>
            <input type="text" class="ef-title" value="${escapeHtml(t.title || "")}" />
          </label>
          <label class="ef-row"><span class="ef-lab">마감</span>
            <input type="date" class="ef-due" value="${escapeHtml(due)}" />
            <span class="ef-hint">비우면 Inbox</span>
          </label>
          <label class="ef-row"><span class="ef-lab">프로젝트</span>
            <select class="ef-project">${projectOptionsHtml(pid)}</select>
          </label>
          <label class="ef-row ef-row-block"><span class="ef-lab">메모</span>
            <textarea class="ef-note" rows="2">${escapeHtml(t.note || "")}</textarea>
          </label>
          <div class="ef-actions">
            <button type="button" class="ef-save" data-task-id="${escapeHtml(t.id)}">저장</button>
            <button type="button" class="ef-cancel" data-task-id="${escapeHtml(t.id)}">취소</button>
          </div>
        </div>
      </div>
    `;
  }

  function bindTaskActions(_container) {
    // 체크박스/편집 모두 전역 위임 핸들러에서 처리됨
  }

  // ============ 편집 폼 토글 / 저장 ============
  // task_id → project_id 역인덱스 (project_id 가 byDay/inbox 항목에 없을 때 보강)
  const _projectIdOf = (() => {
    const m = {};
    for (const pid of Object.keys(projects)) {
      for (const t of (projects[pid].tasks || [])) {
        if (t && t.id) m[t.id] = pid;
      }
    }
    return m;
  })();

  function findTaskById(id) {
    let base = null;
    // 1) 캐시 (모달에 한 번이라도 렌더된 task)
    if (_taskCache[id]) base = _taskCache[id];
    // 2) inbox 전역
    if (!base) {
      const fi = inbox.find(x => x.id === id);
      if (fi) base = fi;
    }
    // 3) byDay
    if (!base) {
      for (const iso of Object.keys(byDay)) {
        const f = (byDay[iso] || []).find(x => x.id === id);
        if (f) { base = f; break; }
      }
    }
    // 4) projects.tasks (마지막 fallback, project_id 명시 부여)
    if (!base) {
      for (const pid of Object.keys(projects)) {
        const f = (projects[pid].tasks || []).find(x => x.id === id);
        if (f) { base = Object.assign({}, f, { project_id: pid }); break; }
      }
    }
    if (!base) return null;
    // project_id 가 없으면 역인덱스에서 보강 (inbox/byDay 항목 케이스)
    if (!base.project_id && _projectIdOf[id]) {
      base = Object.assign({}, base, { project_id: _projectIdOf[id] });
    }
    return base;
  }

  function swapToEdit(rowEl) {
    const tid = rowEl.dataset.taskId;
    const t = findTaskById(tid);
    if (!t) { toast("task 데이터를 찾을 수 없음", "err"); return; }
    const tmp = document.createElement("div");
    tmp.innerHTML = taskEditFormHtml(t).trim();
    const newRow = tmp.firstElementChild;
    rowEl.replaceWith(newRow);
    const titleInput = newRow.querySelector(".ef-title");
    titleInput?.focus();
    titleInput?.select();
  }

  function swapToView(formEl) {
    const tid = formEl.dataset.taskId;
    const t = findTaskById(tid);
    if (!t) { formEl.remove(); return; }
    const tmp = document.createElement("div");
    tmp.innerHTML = taskItemHtml(t).trim();
    formEl.replaceWith(tmp.firstElementChild);
  }

  async function saveEdit(formEl) {
    const tid = formEl.dataset.taskId;
    const titleInput = formEl.querySelector(".ef-title");
    const dueInput = formEl.querySelector(".ef-due");
    const projSel = formEl.querySelector(".ef-project");
    const noteInput = formEl.querySelector(".ef-note");
    const saveBtn = formEl.querySelector(".ef-save");
    const cancelBtn = formEl.querySelector(".ef-cancel");
    const title = (titleInput.value || "").trim();
    if (!title) { toast("제목 비어있음", "err"); titleInput.focus(); return; }
    const payload = {
      title,
      due_at: dueInput.value || null,
      project_id: projSel.value || null,
      note: noteInput.value || "",
    };
    saveBtn.disabled = true; cancelBtn.disabled = true;
    saveBtn.textContent = "저장 중…";
    try {
      const r = await api(`/api/task/${encodeURIComponent(tid)}/update`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (r && r.task) {
        // 캐시 동기화 (다른 뷰 자동 갱신 직전까지 잠깐 사용)
        _taskCache[tid] = Object.assign({}, _taskCache[tid] || {}, r.task);
      }
      toast("✓ 수정됨", "ok");
      reloadSoon();
    } catch (_) {
      // api() 가 이미 toast — 폼 유지하고 재시도 가능 상태로 복구
      saveBtn.disabled = false; cancelBtn.disabled = false;
      saveBtn.textContent = "저장";
    }
  }

  // 위임: 편집 버튼 / 저장 / 취소 클릭
  document.addEventListener("click", e => {
    const editBtn = e.target.closest(".edit-btn");
    if (editBtn) {
      e.stopPropagation();
      const row = editBtn.closest(".sd-item[data-row='view']");
      if (row) swapToEdit(row);
      return;
    }
    const cancelBtn = e.target.closest(".ef-cancel");
    if (cancelBtn) {
      e.stopPropagation();
      const form = cancelBtn.closest(".sd-item[data-row='edit']");
      if (form) swapToView(form);
      return;
    }
    const saveBtn = e.target.closest(".ef-save");
    if (saveBtn) {
      e.stopPropagation();
      const form = saveBtn.closest(".sd-item[data-row='edit']");
      if (form) saveEdit(form);
      return;
    }
  });


  // ============ 핸들러 바인딩 ============
  // 캘린더 셀
  $$(".cal .c[data-iso]").forEach(c => {
    c.addEventListener("click", () => openDayModal(c.dataset.iso));
  });

  // "오늘 마감" 타일 → 오늘 day modal 열기 (거기서 수정 가능)
  $$("[data-open-day]").forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest(".task-check")) return;  // 체크박스 클릭은 무시
      openDayModal(el.dataset.openDay);
    });
  });

  // 프로젝트 카드
  $$(".proj-tile[data-project-id]").forEach(p => {
    p.addEventListener("click", () => openProjectModal(p.dataset.projectId));
  });

  // Inbox 항목 클릭 → 해당 task 의 일자 모달 (또는 단일 task 액션 모달)
  $$(".task-item[data-task-id]").forEach(row => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => {
      const tid = row.dataset.taskId;
      // inbox 에서 task 찾기
      const t = inbox.find(x => x.id === tid);
      if (!t) return;
      $("#ov-day-title").textContent = `📥 Inbox · ${t.title}`;
      const body = $("#ov-day-body");
      // inbox 항목은 project field 없음 → 보강
      const enriched = Object.assign({}, t, { project: "Inbox" });
      body.innerHTML = taskItemHtml(enriched, 1);
      bindTaskActions(body);
      $("#ov-day").classList.add("on");
    });
  });

  // 언젠가 그룹 모달 (기존 트리거)
  $$("[data-modal]").forEach(el => {
    el.addEventListener("click", () => {
      const id = "ov-" + el.dataset.modal;
      const ov = document.getElementById(id);
      if (ov) ov.classList.add("on");
    });
  });

  // 모달 닫기
  $$(".overlay").forEach(ov => {
    ov.addEventListener("click", e => {
      if (e.target === ov) ov.classList.remove("on");
    });
  });
  $$(".overlay .close").forEach(x => {
    x.addEventListener("click", () => x.closest(".overlay").classList.remove("on"));
  });
  // ESC 닫기
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") $$(".overlay.on").forEach(ov => ov.classList.remove("on"));
  });

  // 필터 chip
  let filter = "all";
  $$("#filter-bar .f").forEach(chip => {
    chip.addEventListener("click", () => {
      $$("#filter-bar .f").forEach(c => c.classList.remove("on"));
      chip.classList.add("on");
      filter = chip.dataset.filter;
      applyFilter();
    });
  });

  function applyFilter() {
    // inbox + day-cell items 만 필터 (단순)
    const showTask = t => {
      if (filter === "all") return true;
      const d = t.dataset.taskId ? null : null;  // not needed for inbox row directly
      const ddayEl = t.querySelector(".dday");
      if (!ddayEl) return true;
      const cls = ddayEl.className;
      if (filter === "today") return cls.includes("urgent");
      if (filter === "week") return cls.includes("urgent") || cls.includes("soon");
      return true;
    };
    $$(".task-item").forEach(t => {
      t.style.display = showTask(t) ? "" : "none";
    });
    // 캘린더 셀도: 다른 날 흐리게
    if (filter === "today") {
      $$(".cal .c[data-iso]").forEach(c => {
        c.style.opacity = c.dataset.iso === todayISO ? "1" : "0.3";
      });
    } else if (filter === "week") {
      const today = new Date(todayISO);
      $$(".cal .c[data-iso]").forEach(c => {
        const d = new Date(c.dataset.iso);
        const diff = (d - today) / 86400000;
        c.style.opacity = (diff >= 0 && diff <= 7) ? "1" : "0.3";
      });
    } else {
      $$(".cal .c[data-iso]").forEach(c => c.style.opacity = "");
    }
  }

  // 상단 입력창 — 챗봇 열기
  const qIn = $("#quick-input");
  const qBtn = $("#quick-add");
  function submitQuick() {
    const text = qIn.value.trim();
    if (!text) {
      openChat();  // 빈 입력이면 그냥 챗봇만 열기
      return;
    }
    qIn.value = "";
    openChat(text);
  }
  qIn?.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); submitQuick(); }
  });
  qBtn?.addEventListener("click", submitQuick);

  // 챗봇 패널 입력
  const chatIn = $("#chat-input");
  const chatSend = $("#chat-send");
  chatIn?.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); sendChat(chatIn.value); chatIn.value = ""; }
  });
  chatSend?.addEventListener("click", () => { sendChat(chatIn.value); chatIn.value = ""; });
  // 빈 상태 예시 칩 클릭 → 그대로 전송
  $("#chat-body")?.addEventListener("click", e => {
    const chip = e.target.closest(".ce-chip");
    if (chip) sendChat(chip.dataset.ex || "");
  });

  // 체크박스 — 완료 표시 (모든 .task-check)
  document.addEventListener("change", async e => {
    if (!e.target.matches(".task-check")) return;
    const tid = e.target.dataset.taskId;
    if (!tid) return;
    e.stopPropagation();
    if (e.target.checked) {
      await actDone(tid);
    } else {
      await actUndo(tid);
    }
  });
  // 체크박스 클릭이 row 클릭으로 bubble up 되지 않게
  document.addEventListener("click", e => {
    if (e.target.matches(".task-check")) e.stopPropagation();
  }, true);

  // ============ Drag & Drop — task 의 due_at 이동 ============
  // source: .cell-title[data-task-id] (캘린더 셀 내부 칩), .task-item[data-task-id] (Inbox row)
  // target: .cal .c[data-iso] (해당 날짜로 이동), .inbox-tile (due_at=null)
  let dragSrc = null;

  document.addEventListener("dragstart", e => {
    const el = e.target.closest("[data-task-id]");
    if (!el || el.getAttribute("draggable") !== "true") return;
    dragSrc = el;
    el.classList.add("dragging");
    try {
      e.dataTransfer.setData("text/plain", el.dataset.taskId);
      e.dataTransfer.effectAllowed = "move";
    } catch (_) {}
  });

  document.addEventListener("dragend", () => {
    if (dragSrc) dragSrc.classList.remove("dragging");
    dragSrc = null;
    $$(".drop-target").forEach(el => el.classList.remove("drop-target"));
  });

  function dropTargetOf(node) {
    return node.closest && node.closest(".cal .c[data-iso], .inbox-tile");
  }

  document.addEventListener("dragover", e => {
    const target = dropTargetOf(e.target);
    if (!target || !dragSrc) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!target.classList.contains("drop-target")) {
      $$(".drop-target").forEach(el => el.classList.remove("drop-target"));
      target.classList.add("drop-target");
    }
  });

  document.addEventListener("dragleave", e => {
    const target = dropTargetOf(e.target);
    if (!target) return;
    if (!target.contains(e.relatedTarget)) target.classList.remove("drop-target");
  });

  document.addEventListener("drop", async e => {
    const target = dropTargetOf(e.target);
    if (!target) return;
    e.preventDefault();
    target.classList.remove("drop-target");
    if (!dragSrc) return;  // 외부 드래그 (텍스트 등) 무시
    const tid = (e.dataTransfer.getData("text/plain") || "").trim();
    if (!tid) return;
    const srcContainer = dropTargetOf(dragSrc);
    const srcIsInbox = !!(srcContainer && srcContainer.matches(".inbox-tile"));
    const tgtIsInbox = target.matches(".inbox-tile");
    if (srcContainer === target) return;  // 동일 컨테이너 = no-op
    if (srcIsInbox && tgtIsInbox) return;  // 의미상 inbox→inbox 도 no-op
    const newDue = tgtIsInbox ? null : target.dataset.iso;
    try {
      await api(`/api/task/${encodeURIComponent(tid)}/update`, {
        method: "POST",
        body: JSON.stringify({ due_at: newDue }),
      });
      toast(newDue ? `📅 ${fmtDate(newDue)} 로 이동` : "📥 Inbox 로 이동", "ok");
      reloadSoon();
    } catch (_) {
      // api() 가 이미 toast 함
    }
  });

  // ============ /api/version polling — 자동 reload ============
  let lastMtime = null;
  async function checkVersion() {
    try {
      const r = await fetch("/api/version", { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      if (lastMtime === null) {
        lastMtime = j.mtime;
        setSyncState("OK");
      } else if (j.mtime !== lastMtime) {
        setSyncState("변경 감지 — 새로고침");
        setTimeout(() => location.reload(), 600);
      } else {
        setSyncState("OK · " + new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }));
      }
    } catch (e) {
      setSyncState("✗ " + e.message);
    }
  }
  function setSyncState(s) {
    const el = $("#sync-state");
    if (el) el.textContent = "sync " + s;
  }
  setInterval(checkVersion, 30000);
  checkVersion();

})();
