/* Safe rendering helpers for the relationship-life briefing. */
(function () {
  function renderBriefing() {
    const box = document.getElementById('briefing');
    const select = document.getElementById('briefNode');
    if (!box || !select) return;
    const nodeId = select.value;
    if (!nodeId) {
      box.textContent = 'اول یک نفر را انتخاب کن.';
      return;
    }
    box.textContent = 'در حال آماده‌سازی…';
    fetch('/api/relationship-life/briefing/' + encodeURIComponent(nodeId) + '/')
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || 'خطا در دریافت اطلاعات');
          return data;
        });
      })
      .then(function (data) {
        box.replaceChildren();
        function line(label, value) {
          const row = document.createElement('div');
          const strong = document.createElement('b');
          strong.textContent = label + ': ';
          row.append(strong, document.createTextNode(String(value || '—')));
          box.append(row);
        }
        line('شخص', data.person);
        line('آخرین تعامل', data.last_interaction || 'ثبت نشده');
        if (data.paused) line('وضعیت', '⛔ پیشنهاد تماس متوقف است');
        if (data.boundaries) line('مرزها', data.boundaries);
        line('شناخت‌ها', (data.facts || []).map(function (item) {
          return item.value + ' (' + item.confidence + '٪؛ ' + item.source + ')';
        }).join('، '));
        line('قول‌ها', (data.commitments || []).map(function (item) { return item.text; }).join('، '));
        line('حساب باز', (data.debts || []).map(function (item) { return item.amount; }).join('، '));
      })
      .catch(function (error) { box.textContent = error.message || 'خطا در ارتباط با سرور'; });
  }
  window.loadBriefing = renderBriefing;
}());

/* The command palette also receives JSON; keep its renderer DOM-only. */
(function () {
  window.loadPalette = async function () {
    const input = document.getElementById('fgPaletteInput');
    const results = document.getElementById('fgPaletteResults');
    if (!input || !results) return;
    try {
      const response = await fetch('/api/platform/command-palette/?q=' + encodeURIComponent(input.value));
      const data = await response.json();
      results.replaceChildren();
      (data.results || []).forEach(function (item) {
        let target;
        try { target = new URL(item.url || '/', window.location.origin); } catch (_) { return; }
        if (target.origin !== window.location.origin) return;
        const link = document.createElement('a');
        link.href = target.pathname + target.search + target.hash;
        link.style.cssText = 'display:flex;gap:10px;padding:10px;border-radius:10px;color:var(--text);text-decoration:none';
        const icon = document.createElement('span');
        icon.textContent = item.icon || '';
        const content = document.createElement('span');
        const title = document.createElement('b');
        title.textContent = item.title || '';
        const subtitle = document.createElement('small');
        subtitle.style.cssText = 'display:block;color:var(--text-muted)';
        subtitle.textContent = item.subtitle || '';
        content.append(title, subtitle);
        link.append(icon, content);
        results.append(link);
      });
    } catch (_) {
      results.textContent = 'خطا در بارگذاری';
    }
  };
}());
