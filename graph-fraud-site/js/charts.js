/* =====================================================================
   charts.js -- tiny dependency-free SVG chart helpers with hover tooltips
   ===================================================================== */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e;
  }
  let tip = null;
  function tooltip() {
    if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; document.body.appendChild(tip); }
    return tip;
  }
  function showTip(html, e) { const t = tooltip(); t.innerHTML = html; t.style.display = 'block'; const w = t.offsetWidth; let x = e.clientX + 14, y = e.clientY + 14; if (x + w > innerWidth - 8) x = e.clientX - w - 14; t.style.left = x + 'px'; t.style.top = y + 'px'; }
  function hideTip() { if (tip) tip.style.display = 'none'; }

  /* ---- multi-series line chart. cfg: {el, x:[], xLabel, yLabel, yMax, series:[{name,color,y:[],dash,hi}], xFmt, yFmt, xCat} ---- */
  function lineChart(cfg) {
    const host = typeof cfg.el === 'string' ? document.querySelector(cfg.el) : cfg.el; host.innerHTML = '';
    const W = cfg.w || 560, H = cfg.h || 300, m = { l: 48, r: 14, t: 14, b: 44 };
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', 'aria-label': cfg.title || 'chart' }, host);
    const xs = cfg.x, n = xs.length; const yMax = cfg.yMax != null ? cfg.yMax : 1;
    const X = (i) => m.l + (cfg.xCat || cfg.xIndex ? (i / (n - 1)) : ((xs[i] - xs[0]) / (xs[n - 1] - xs[0]))) * (W - m.l - m.r);
    const Y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);
    for (let t = 0; t <= 4; t++) { const v = (yMax * t) / 4; el('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: 'grid' }, svg); el('text', { x: m.l - 7, y: Y(v) + 4, class: 'tick', 'text-anchor': 'end' }, svg, (cfg.yFmt || ((q) => q.toFixed(2)))(v)); }
    xs.forEach((xv, i) => { el('text', { x: X(i), y: H - m.b + 17, class: 'tick', 'text-anchor': 'middle' }, svg, (cfg.xFmt || String)(xv)); el('line', { x1: X(i), x2: X(i), y1: H - m.b, y2: H - m.b + 4, class: 'axis' }, svg); });
    el('line', { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: 'axis' }, svg); el('line', { x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, class: 'axis' }, svg);
    if (cfg.xLabel) el('text', { x: (m.l + W - m.r) / 2, y: H - 6, class: 'axlabel', 'text-anchor': 'middle' }, svg, cfg.xLabel);
    if (cfg.yLabel) el('text', { x: 12, y: (m.t + H - m.b) / 2, class: 'axlabel', 'text-anchor': 'middle', transform: `rotate(-90 12 ${(m.t + H - m.b) / 2})` }, svg, cfg.yLabel);
    if (cfg.refLine != null) { el('line', { x1: m.l, x2: W - m.r, y1: Y(cfg.refLine.v), y2: Y(cfg.refLine.v), class: 'ref' }, svg); el('text', { x: W - m.r - 4, y: Y(cfg.refLine.v) - 5, class: 'tick', 'text-anchor': 'end' }, svg, cfg.refLine.label || ''); }
    cfg.series.forEach((s) => {
      const pts = s.y.map((v, i) => (v == null ? null : [X(i), Y(v)]));
      let d = ''; pts.forEach((p, i) => { if (p) d += (d && pts[i - 1] ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); });
      el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.hi ? 3.4 : 2.2, 'stroke-dasharray': s.dash || '', 'stroke-linejoin': 'round', 'stroke-linecap': 'round', class: 'ln', 'data-series': s.name }, svg);
      pts.forEach((p, i) => { if (p) el('circle', { cx: p[0], cy: p[1], r: s.hi ? 4.2 : 3, fill: s.color, class: 'pt' }, svg); });
    });
    // hover capture columns
    const guide = el('line', { y1: m.t, y2: H - m.b, class: 'guide', style: 'display:none' }, svg);
    xs.forEach((xv, i) => {
      const w = (W - m.l - m.r) / Math.max(n - 1, 1); const r = el('rect', { x: X(i) - w / 2, y: m.t, width: w, height: H - m.t - m.b, fill: 'transparent' }, svg);
      r.addEventListener('pointermove', (e) => { guide.setAttribute('x1', X(i)); guide.setAttribute('x2', X(i)); guide.style.display = '';
        const rows = cfg.series.map((s) => `<div><i style="background:${s.color}"></i>${s.name}<b>${s.y[i] == null ? '–' : (cfg.yFmt || ((q) => q.toFixed(3)))(s.y[i], true)}</b></div>`).join('');
        showTip(`<div class="tt">${cfg.xLabel ? cfg.xLabel + ' = ' : ''}${(cfg.xFmt || String)(xv)}</div>${rows}`, e); });
      r.addEventListener('pointerleave', () => { guide.style.display = 'none'; hideTip(); });
    });
    if (cfg.legend !== false) { const lg = document.createElement('div'); lg.className = 'legend'; cfg.series.forEach((s) => { const b = document.createElement('span'); b.innerHTML = `<i style="background:${s.color}"></i>${s.name}`; lg.appendChild(b); }); host.appendChild(lg); }
    return svg;
  }

  /* ---- grouped bar chart. cfg: {el, cats:[], series:[{name,color,v:[]}], yMax} ---- */
  function barChart(cfg) {
    const host = typeof cfg.el === 'string' ? document.querySelector(cfg.el) : cfg.el; host.innerHTML = '';
    const W = cfg.w || 560, H = cfg.h || 300, m = { l: 48, r: 14, t: 14, b: cfg.rot ? 76 : 48 };
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img' }, host);
    const yMax = cfg.yMax || 1; const Y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);
    for (let t = 0; t <= 4; t++) { const v = (yMax * t) / 4; el('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: 'grid' }, svg); el('text', { x: m.l - 7, y: Y(v) + 4, class: 'tick', 'text-anchor': 'end' }, svg, v.toFixed(2)); }
    const nc = cfg.cats.length, gw = (W - m.l - m.r) / nc, ns = cfg.series.length, bw = Math.min(34, (gw * 0.78) / ns);
    cfg.cats.forEach((c, i) => {
      const cx = m.l + gw * i + gw / 2;
      const t = el('text', { x: cx, y: H - m.b + 17, class: 'tick', 'text-anchor': cfg.rot ? 'end' : 'middle', transform: cfg.rot ? `rotate(-28 ${cx} ${H - m.b + 17})` : '' }, svg, c);
      cfg.series.forEach((s, j) => {
        const v = s.v[i]; if (v == null) return; const x = cx - (ns * bw) / 2 + j * bw; const r = el('rect', { x, y: Y(v), width: bw - 3, height: H - m.b - Y(v), fill: s.color, rx: 3, class: 'bar' }, svg);
        r.addEventListener('pointermove', (e) => showTip(`<div class="tt">${c}</div><div><i style="background:${s.color}"></i>${s.name}<b>${v.toFixed(3)}</b></div>`, e)); r.addEventListener('pointerleave', hideTip);
        if (cfg.labels !== false && bw > 18) el('text', { x: x + (bw - 3) / 2, y: Y(v) - 4, class: 'bval', 'text-anchor': 'middle' }, svg, v.toFixed(2).replace(/^0/, ''));
      });
    });
    el('line', { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: 'axis' }, svg); el('line', { x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, class: 'axis' }, svg);
    if (cfg.refLine) { el('line', { x1: m.l, x2: W - m.r, y1: Y(cfg.refLine.v), y2: Y(cfg.refLine.v), class: 'ref' }, svg); el('text', { x: W - m.r - 4, y: Y(cfg.refLine.v) - 5, class: 'tick', 'text-anchor': 'end' }, svg, cfg.refLine.label || ''); }
    if (cfg.legend !== false && ns > 1) { const lg = document.createElement('div'); lg.className = 'legend'; cfg.series.forEach((s) => { const b = document.createElement('span'); b.innerHTML = `<i style="background:${s.color}"></i>${s.name}`; lg.appendChild(b); }); host.appendChild(lg); }
    return svg;
  }

  function heatBg(a) { const col = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#ffb454').trim(); let r = 255, g = 180, b = 84; const m = /^#([0-9a-f]{6})$/i.exec(col); if (m) { r = parseInt(m[1].slice(0, 2), 16); g = parseInt(m[1].slice(2, 4), 16); b = parseInt(m[1].slice(4), 16); } return `rgba(${r},${g},${b},${Math.max(0.1, Math.min(1, a)).toFixed(2)})`; }
  /* ---- heatmap. cfg: {el, rows:[], cols:[], v:[[]], fmt} (null cell = n/a) ---- */
  function heatmap(cfg) {
    const host = typeof cfg.el === 'string' ? document.querySelector(cfg.el) : cfg.el; host.innerHTML = '';
    const t = document.createElement('table'); t.className = 'heat';
    const th = document.createElement('tr'); th.innerHTML = '<th></th>' + cfg.cols.map((c) => `<th>${c}</th>`).join(''); t.appendChild(th);
    cfg.rows.forEach((r, i) => { const tr = document.createElement('tr'); tr.innerHTML = `<th>${r}</th>` + cfg.v[i].map((v, j) => {
      if (v == null) return `<td class="na" title="not stated numerically in the paper">~0</td>`;
      const a = Math.max(0.06, v); return `<td style="--a:${a};background:${heatBg(a)}" data-t="${r} → ${cfg.cols[j]}: ${(v * 100).toFixed(0)}% of those accounts found">${v.toFixed(2)}</td>`; }).join(''); t.appendChild(tr); });
    host.appendChild(t);
    t.querySelectorAll('td[data-t]').forEach((td) => { td.addEventListener('pointermove', (e) => showTip(td.dataset.t, e)); td.addEventListener('pointerleave', hideTip); });
  }

  root.CH = { el, lineChart, barChart, heatmap, showTip, hideTip };
})(window);
