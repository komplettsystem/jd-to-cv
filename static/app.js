// Chat, live status, stamp bookkeeping, links between a requirement and its sentence, letter tools and the
// trace replay. No libraries. Every piece of text is inserted with textContent.
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const root = document.documentElement;
  const $ = (sel, from) => (from || document).querySelector(sel);
  const $$ = (sel, from) => Array.from((from || document).querySelectorAll(sel));

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  // Nothing is written to the visitor's device: no cookies, local storage or session storage. The colour choice below
  // lasts for the page only, and the stamps land each time a report is opened.

  // --- settings: which boxes are saved ---
  // A box shows what is stored when the page loads. Its Save button stays off until a field differs from the stored value
  // (the value the server put in the page, so text a browser restores after a reload counts as a change), and the box says
  // "Saved" or "Unsaved changes". Without this script the buttons simply stay on.
  $$('section[data-track]').forEach((sec) => {
    const state = $('[data-save-state]', sec), form = $('form', sec);
    const save = form && $('.submit-row button:not(.quiet)', form);
    if (!state || !save) return;
    const changed = (f) => {
      if (f.type === 'file') return f.files.length > 0;
      if (f.type === 'checkbox' || f.type === 'radio') return f.checked !== f.defaultChecked;
      if (f.tagName === 'SELECT') return Array.from(f.options).some((o) => o.selected !== o.defaultSelected);
      return f.value !== f.defaultValue;
    };
    const update = () => {
      const dirty = $$('input:not([type=hidden]), textarea, select', form).some(changed);
      state.textContent = dirty ? 'Unsaved changes: click Save to keep them' : 'Saved';
      state.hidden = false;
      save.disabled = !dirty;
      sec.classList.toggle('unsaved', dirty);
    };
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
  });

  // --- colour theme ---
  const themeBtn = $('[data-theme-toggle]');
  if (themeBtn) {
    const current = () => root.getAttribute('data-theme') ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const label = () => { themeBtn.textContent = current() === 'dark' ? 'Use light colours' : 'Use dark colours'; };
    label();
    themeBtn.hidden = false;
    themeBtn.addEventListener('click', () => {
      const next = current() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      label();
    });
  }

  // --- a link into a folded section opens it ---
  function openTarget() {
    if (!location.hash) return;
    let node = null;
    try { node = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (e) { return; }
    for (let d = node?.closest('details'); d; d = d.parentElement?.closest('details')) d.open = true;
    if (node) node.scrollIntoView();
  }
  window.addEventListener('hashchange', openTarget);
  if (location.hash) openTarget();

  // --- requirement row <-> its sentence in the posting ---
  function link(on, n) {
    $$(`[data-req="${n}"]`).forEach(x => x.classList.toggle('is-linked', on));
  }
  $$('[data-req]').forEach(node => {
    const n = node.dataset.req;
    node.addEventListener('mouseenter', () => link(true, n));
    node.addEventListener('mouseleave', () => link(false, n));
    node.addEventListener('focusin', () => link(true, n));
    node.addEventListener('focusout', () => link(false, n));
  });

  // --- confirm before deleting ---
  document.addEventListener('submit', e => {
    const msg = e.target.dataset?.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // --- letter: copy and print ---
  document.addEventListener('click', e => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const src = $(copy.dataset.copy);
      const text = src ? (src.value !== undefined ? src.value : src.innerText) : '';
      const done = ok => {
        const old = copy.textContent;
        copy.textContent = ok ? 'Copied' : 'Could not copy. Select the text instead.';
        setTimeout(() => { copy.textContent = old; }, 2200);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => done(true), () => done(false));
      else done(false);
    }
    const print = e.target.closest('[data-print]');
    if (print) {
      const paper = $(print.dataset.print);
      if (!paper) return;
      $$('.is-printing').forEach(x => x.classList.remove('is-printing'));
      paper.classList.add('is-printing');
      root.classList.add('print-letter');
      window.print();
    }
  });
  window.addEventListener('afterprint', () => root.classList.remove('print-letter'));

  // --- trace replay ---
  const replay = $('.replay');
  if (replay) {
    const spans = $$('.wf .span');
    const status = $('.replay-status', replay);
    const playBtn = $('[data-replay="play"]', replay);
    let at = -1, timer = null;
    const stop = () => { clearInterval(timer); timer = null; if (playBtn) playBtn.textContent = 'Play'; };
    function show(i) {
      at = Math.max(0, Math.min(spans.length - 1, i));
      spans.forEach((s, k) => {
        s.classList.toggle('is-future', k > at);
        s.classList.toggle('is-now', k === at);
        const d = $('details', s);
        if (d) d.open = k === at;
      });
      const name = $('.span-title', spans[at])?.textContent || '';
      status.textContent = `Step ${at + 1} of ${spans.length}: ${name}`;
      spans[at].scrollIntoView({ block: 'nearest' });
      if (at === spans.length - 1) stop();
    }
    function all() {
      stop(); at = -1;
      spans.forEach(s => { s.classList.remove('is-future', 'is-now'); const d = $('details', s); if (d) d.open = false; });
      status.textContent = `${spans.length} steps, all shown`;
    }
    replay.addEventListener('click', e => {
      const b = e.target.closest('[data-replay]');
      if (!b || !spans.length) return;
      const what = b.dataset.replay;
      if (what === 'next') { stop(); show(at + 1); }
      else if (what === 'prev') { stop(); show(at - 1); }
      else if (what === 'all') all();
      else if (what === 'play') {
        if (timer) { stop(); return; }
        show(at >= spans.length - 1 ? 0 : at + 1);
        if (at < spans.length - 1) { timer = setInterval(() => show(at + 1), 2400); playBtn.textContent = 'Pause'; }
      }
    });
  }

  // --- live status and chat ---
  const report = document.getElementById('report');
  const chat = document.getElementById('chat');
  const stateUrl = chat?.dataset.stateUrl || report?.dataset.stateUrl;
  const log = document.getElementById('chat-log');
  let lastId = Number(chat?.dataset.lastId || 0);
  let polling = false;

  function chip(text) {
    const b = el('button', 'chip', text);
    b.type = 'button';
    b.dataset.question = text;
    return b;
  }

  // What the chat bot asked the match engine and what it answered, with the raw messages one click away.
  const VERDICT_LABEL = { met: 'Met', partial: 'Partly', not_on_record: 'Not shown' };

  // A matcher exchange also shows, outside the panel, each statement with the report's label, the CV line and the reason.
  function renderMatcherExchange(ex) {
    const wrap = document.createDocumentFragment();
    const r = ex.response || {};
    ((r.result || {}).structuredContent || {}).results?.forEach(item => {
      const box = el('div', 'matcher-item');
      const head = el('p', 'matcher-statement');
      head.append(el('span', 'stamp stamp-' + item.verdict + ' stamp-sm', VERDICT_LABEL[item.verdict] || item.verdict), el('span', null, item.statement));
      box.appendChild(head);
      if (item.line) {
        const q = el('q', 'cv-quote');
        q.append(el('span', 'cv-line', item.line), el('span', 'cv-src', 'CV, ' + item.section));
        box.appendChild(q);
      }
      box.appendChild(el('p', 'matcher-reason', item.reason));
      wrap.appendChild(box);
    });
    const d = el('details', 'exchange');
    d.appendChild(el('summary', null, 'The chat bot asked the matcher'));
    const call = (ex.request || {}).params || {};
    const asked = el('p', 'exchange-line');
    asked.append(el('span', 'exchange-who', 'Chat bot → matcher'), ' ' + call.name + ' ' + JSON.stringify(call.arguments || {}));
    const reply = el('p', 'exchange-line');
    reply.append(el('span', 'exchange-who', 'Matcher → chat bot'), ' ' + (((r.result || {}).content || [{}])[0].text || ''));
    d.append(asked, reply, el('pre', 'exchange-raw', JSON.stringify(ex.request, null, 2) + '\n' + JSON.stringify(ex.response, null, 2)),
      el('p', 'exchange-note', 'The matcher is a model that runs the same judging step as the report, with the same rules. It read the whole CV for these statements. What it says is a reading of the CV: it never changes the report or the percentage.'));
    wrap.appendChild(d);
    return wrap;
  }

  function renderExchange(ex) {
    if (ex.to === 'matcher') return renderMatcherExchange(ex);
    const d = el('details', 'exchange');
    d.appendChild(el('summary', null, 'The chat bot asked the match engine'));
    const call = ex.request.params || {};
    const asked = el('p', 'exchange-line');
    asked.append(el('span', 'exchange-who', 'Chat bot → match engine'), ' ' + call.name + ' ' + JSON.stringify(call.arguments || {}));
    const reply = el('p', 'exchange-line');
    const r = ex.response || {};
    reply.append(el('span', 'exchange-who', 'Match engine → chat bot'),
      ' ' + (r.result ? (r.result.content[0] || {}).text : (r.error || {}).message));
    d.append(asked, reply, el('pre', 'exchange-raw', JSON.stringify(ex.request, null, 2) + '\n' + JSON.stringify(ex.response, null, 2)),
      el('p', 'exchange-note', 'The match engine is plain code, not a second model: it owns the scoring and the chat bot only asks it.'));
    return d;
  }

  function renderMessage(m) {
    if (!log) return;
    let node = log.querySelector(`[data-id="${Number(m.id)}"]`);
    const fresh = !node;
    if (fresh) { node = el('div'); node.dataset.id = m.id; log.appendChild(node); }
    node.className = `msg msg-${m.role === 'user' ? 'user' : 'assistant'}` + (m.status === 'failed' ? ' msg-failed' : '');
    node.replaceChildren();
    const skeptic = m.mode === 'skeptic';
    node.appendChild(el('span', 'msg-label',
      m.role === 'user' ? (skeptic ? 'You asked, skeptic on' : 'You asked') : (skeptic ? 'Answer, arguing against the match' : 'Answer')));
    const text = el('div', 'msg-text');
    if (m.status === 'pending') {
      const dots = el('span', 'dots');
      dots.setAttribute('aria-hidden', 'true');
      dots.append(el('i'), el('i'), el('i'));
      text.append(dots, el('span', 'sr', 'Writing an answer'));
    } else if (m.status === 'failed' && !m.content) {
      text.textContent = m.error || 'This question could not be answered. Ask it again, or word it differently.';
    } else {
      text.textContent = m.content;
    }
    node.appendChild(text);
    (m.evidence || []).forEach(e => {
      const q = el('q', 'cv-quote');
      q.appendChild(el('span', 'cv-line', e.text));
      q.appendChild(el('span', 'cv-src', 'CV, ' + e.section));
      node.appendChild(q);
    });
    (m.exchanges || []).forEach(ex => node.appendChild(renderExchange(ex)));
    (m.suggestions || []).forEach(s => node.appendChild(chip(s)));
    if (fresh || m.status !== 'pending') log.scrollTop = log.scrollHeight;
  }

  const wait = ms => new Promise(r => setTimeout(r, ms));
  async function poll(onState, until) {
    if (polling || !stateUrl) return;
    polling = true;
    try {
      for (let fails = 0; fails < 5;) {
        await wait(2000);
        let s = null;
        try {
          const res = await fetch(stateUrl + '?after=' + lastId, { headers: { Accept: 'application/json' } });
          if (res.status === 404) return;
          if (res.ok) s = await res.json();
        } catch (e) { /* network hiccup: try again */ }
        if (!s) { fails += 1; continue; }
        fails = 0;
        onState(s);
        if (until(s)) return;
      }
      const note = document.getElementById('chat-error');
      if (note) { note.textContent = 'Lost contact with the server. Reload the page to see the answer.'; note.hidden = false; }
    } finally { polling = false; }
  }
  const resultsRunning = s => s.results.some(r => ['queued', 'running'].includes(r.status));
  const working = s => resultsRunning(s) || s.letters.some(l => ['queued', 'running'].includes(l.status));
  const showAnswers = s => s.messages.forEach(m => { if (m.role === 'assistant') renderMessage(m); });

  // The role title and company are found while the report is still being made: show them as soon as they are known.
  const titleEl = document.getElementById('report-title');
  const postingEl = document.querySelector('[data-live-posting]');
  const companyEl = document.querySelector('[data-live-company]');
  function showTitle(s) {
    if (!s.title) return;
    if (titleEl) titleEl.textContent = s.title + (s.company ? ', ' + s.company : '');
    if (postingEl) postingEl.textContent = s.title;
    if (companyEl) { companyEl.textContent = s.company || ''; companyEl.hidden = !s.company; }
  }

  const pageBusy = !!document.querySelector('#report .badge.queued, #report .badge.running, #letter .badge.queued, #letter .badge.running');
  if (pageBusy) {
    // The report or a letter is still being produced: reload when it is ready.
    // Reload as soon as the report itself is ready, without waiting for the letter that follows it; if the page was opened
    // while only a letter was being written, wait for the letter.
    let first = null;
    poll(s => { showTitle(s); showAnswers(s); },
         s => { first = first ?? resultsRunning(s); return first ? !resultsRunning(s) : !working(s); }).then(() => location.reload());
  } else if (log?.querySelector('.msg-pending')) {
    poll(showAnswers, s => !s.busy);
  }

  if (!chat) return;
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-q');
  const errorBox = document.getElementById('chat-error');
  const leftNote = document.getElementById('chat-left');
  const chips = chat.querySelector('.chips');
  const send = form.querySelector('button');
  if (log) log.scrollTop = log.scrollHeight;

  function showError(msg) { errorBox.textContent = msg; errorBox.hidden = !msg; }

  // The suggested questions: two are shown, and a question that was asked (by a click or typed) gives its place to the next one.
  function refreshChips() {
    if (!chips) return;
    const asked = new Set([...log.querySelectorAll('.msg-user .msg-text')].map(n => n.textContent.trim()));
    let shown = 0;
    const room = Number(chips.dataset.show) || 2;
    chips.querySelectorAll('[data-question]').forEach(b => {
      const show = !asked.has(b.dataset.question) && shown < room;
      b.hidden = !show;
      if (show) shown += 1;
    });
    chips.hidden = shown === 0;
  }

  // "You have 3 questions left", shown only when few are left (the server renders it hidden while there are plenty).
  function updateLeft(delta) {
    if (!leftNote) return;
    const left = Math.max(0, Number(leftNote.dataset.left) + delta);
    leftNote.dataset.left = String(left);
    leftNote.textContent = left <= 0 ? 'You have used all your questions for this report.'
      : left === 1 ? 'You have 1 question left.' : 'You have ' + left + ' questions left.';
    leftNote.hidden = left > 5;
  }

  // The question box is a textarea that grows with the text (up to six lines); Enter sends, Shift+Enter starts a new line.
  function fit() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight + 2, 144) + 'px'; }
  input.addEventListener('input', fit);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  refreshChips();

  async function ask(question) {
    showError('');
    const skeptic = document.getElementById('chat-skeptic')?.checked || false;
    send.disabled = true;
    let res, body = {};
    try {
      res = await fetch(chat.dataset.chatUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ question, skeptic }),
      });
      body = await res.json().catch(() => ({}));
    } catch (e) {
      showError('The question did not reach the server. Check your connection and ask again.');
      send.disabled = false;
      return;
    }
    send.disabled = false;
    if (!res.ok) { showError(body.error || 'The question could not be sent. Reload the page and ask again.'); return; }
    const mode = skeptic ? 'skeptic' : 'normal';
    renderMessage({ id: body.user_id, role: 'user', content: question, status: 'done', mode });
    renderMessage({ id: body.assistant_id, role: 'assistant', content: '', status: 'pending', mode });
    lastId = body.user_id;
    input.value = '';
    fit();
    updateLeft(-1);
    refreshChips();
    poll(showAnswers, s => s.messages.some(m => m.id === body.assistant_id && m.status !== 'pending'));
  }

  form.addEventListener('submit', e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) { showError('Write a question first.'); input.focus(); return; }
    ask(q);
  });
  chat.addEventListener('click', e => {
    const b = e.target.closest('[data-question]');
    if (b) { input.value = b.dataset.question; ask(b.dataset.question); }
  });
})();

// A form marked data-refresh="#id" is sent in the background and only that part of the page is replaced with the new version, so
// the reader stays where they are (for example when a model is switched on or off in a long list). Nothing is stored; if the
// background request fails, the form is sent the usual way.
(function () {
  document.addEventListener('submit', async e => {
    const form = e.target;
    const selector = form.matches && form.getAttribute('data-refresh');
    if (!selector) return;
    e.preventDefault();
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
      if (!response.ok) throw new Error('rejected');
      const fresh = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector(selector);
      const current = document.querySelector(selector);
      if (!fresh || !current) throw new Error('missing');
      current.replaceWith(fresh);
    } catch (err) {
      form.submit();
    }
  });
})();

// A button marked data-toggle-all="#id" opens or closes every <details> inside that element (the change log on the evals page).
(function () {
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('[data-toggle-all]');
    if (!b) return;
    const list = document.querySelector(b.getAttribute('data-toggle-all'));
    if (!list) return;
    const open = b.dataset.open !== '1';
    list.querySelectorAll('details').forEach(d => { d.open = open; });
    b.dataset.open = open ? '1' : '0';
    b.textContent = open ? 'Close all entries' : 'Open all entries';
  });
})();
