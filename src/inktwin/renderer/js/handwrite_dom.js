(async function() {
  const CFG = window.__INKTWIN_CFG__ || {};
  const GRID_LOCK = !!CFG.gridLock;
  const MJ = CFG.mathJitter;
  const TJ = CFG.textJitter;
  const CN_SX = CFG.cnSx;
  const CN_SY = CFG.cnSy;
  const CN_SKEW = CFG.cnSkew;
  const CN_MR = CFG.cnMr;
  const USE_PERSONAL_CJK = CFG.usePersonalCjk;
  const QUIRKS_ENABLED = CFG.quirksEnabled;
  const ELASTIC_AMP = (CFG.elasticAmp !== undefined) ? Number(CFG.elasticAmp) : 0.026;
  const LAYOUT_MODE = CFG.layoutMode;
  const PERSONAL_URL = CFG.personalUrl;
  const SYNTH_CJK = CFG.synthCjk;
  const BASE_FONT_PX = CFG.baseFontPx;
  const TARGET_STROKE_PX = 0.74;
  const SVG_NS = "http://www.w3.org/2000/svg";

  let rngState = (CFG.seed >>> 0) || 123456789;
  function rand() {
    rngState |= 0;
    rngState = (rngState + 0x6D2B79F5) | 0;
    let t = Math.imul(rngState ^ (rngState >>> 15), 1 | rngState);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function randSym(amp) {
    return ((rand() + rand() + rand()) / 3 - 0.5) * 2 * amp;
  }

  let PG = null;
  if (PERSONAL_URL) {
    try {
      const resp = await fetch(PERSONAL_URL);
      if (resp.ok) PG = await resp.json();
    } catch (e) {
      PG = null;
    }
  }
  // 合并学到的 Andy 个人连笔合成汉字库（仅补充真实库未收录的生字，零第三方内置字体！）
  if (USE_PERSONAL_CJK && SYNTH_CJK && Object.keys(SYNTH_CJK).length > 0) {
    if (!PG) PG = Object.create(null);
    for (const [ch, entry] of Object.entries(SYNTH_CJK)) {
      if (!PG[ch] || !PG[ch].v || !PG[ch].v.length) {
        PG[ch] = entry;
      }
    }
  }
  const lastPick = Object.create(null);
  const seenCount = Object.create(null);

  function pickVariant(ch) {
    if (!PG || !PG[ch]) return null;
    const entry = PG[ch];
    if (entry.cat === 'cjk' && !USE_PERSONAL_CJK) return null;
    const arr = entry.v;
    if (!arr || !arr.length) return null;
    let idx = Math.floor(rand() * arr.length);
    if (arr.length > 1 && idx === lastPick[ch]) {
      idx = (idx + 1 + Math.floor(rand() * (arr.length - 1))) % arr.length;
    }
    lastPick[ch] = idx;
    return { cat: entry.cat, v: arr[idx], vidx: idx, nVars: arr.length };
  }

  const ELASTIC_EXEMPT_CATS = new Set(['delim', 'op_eq', 'op_minus', 'dot', 'dots_h', 'accent_bar', 'accent_hat']);
  const ELASTIC_EXEMPT_CHARS = new Set(['=', '+', '-', '−', '×', '÷', '±', '|', '∣', '∥', '‖', '(', ')', '[', ']', '{', '}', '/', '.', ',', ':', ';', '·', '…']);

  function makeElasticField(amp) {
    const a = Math.min(0.038, Math.max(0.0, amp));
    return {
      dx00: randSym(a), dy00: randSym(a),
      dx10: randSym(a), dy10: randSym(a),
      dx01: randSym(a), dy01: randSym(a),
      dx11: randSym(a), dy11: randSym(a),
      bx: randSym(a * 0.65),
      by: randSym(a * 0.65),
    };
  }

  function deformPathD(dStr, w, h, f) {
    if (!dStr || w <= 1 || h <= 1) return dStr;
    const tokens = dStr.match(/[MLQCZmlqcz]|-?\d*\.?\d+(?:e[+-]?\d+)?/gi);
    if (!tokens) return dStr;
    for (let i = 0; i < tokens.length; i++) {
      const tk = tokens[i];
      if (/^[A-Za-z]$/.test(tk) && !'MLQCZ'.includes(tk)) return dStr;
    }
    const out = [];
    let idx = 0;
    let cmd = '';
    function tx(x, y) {
      const u = Math.max(0, Math.min(1, x / w));
      const v = Math.max(0, Math.min(1, y / h));
      const s = (1 - u) * (1 - v) * f.dx00 + u * (1 - v) * f.dx10 + (1 - u) * v * f.dx01 + u * v * f.dx11;
      const bow = Math.sin(Math.PI * v) * f.bx;
      return +(x + (s + bow) * w).toFixed(2);
    }
    function ty(x, y) {
      const u = Math.max(0, Math.min(1, x / w));
      const v = Math.max(0, Math.min(1, y / h));
      const s = (1 - u) * (1 - v) * f.dy00 + u * (1 - v) * f.dy10 + (1 - u) * v * f.dy01 + u * v * f.dy11;
      const bow = Math.sin(Math.PI * u) * f.by;
      return +(y + (s + bow) * h).toFixed(2);
    }
    while (idx < tokens.length) {
      const tk = tokens[idx];
      if (/^[MLQCZ]$/.test(tk)) {
        cmd = tk;
        out.push(cmd);
        idx++;
        continue;
      }
      const nPairs = cmd === 'Q' ? 2 : (cmd === 'C' ? 3 : 1);
      if (idx + nPairs * 2 > tokens.length) return dStr;
      for (let p = 0; p < nPairs; p++) {
        const px = parseFloat(tokens[idx + p * 2]);
        const py = parseFloat(tokens[idx + p * 2 + 1]);
        if (!Number.isFinite(px) || !Number.isFinite(py)) return dStr;
        out.push(tx(px, py), ty(px, py));
      }
      idx += nPairs * 2;
      if (cmd === 'M') cmd = 'L';
    }
    return out.join(' ');
  }

  // v11 清瘦 StarNote 笔锋校准：仅对真正偏细的小笔迹微补（~0.74px），绝不把整页强行加粗！
  function applyUniformInkCompensation(svgEl, pathEl, v, hPx, targetPx, isCjk) {
    if (!v.fill) {
      pathEl.setAttribute('fill', 'none');
      pathEl.setAttribute('stroke', 'currentColor');
      pathEl.setAttribute('vector-effect', 'non-scaling-stroke');
      pathEl.style.setProperty('stroke', 'currentColor', 'important');
      pathEl.style.setProperty('stroke-width', `${(targetPx + randSym(0.04)).toFixed(2)}px`, 'important');
      return;
    }
    pathEl.setAttribute('fill', 'currentColor');
    const tNorm = typeof v.t === 'number' ? v.t : 4.8;
    const estRenderedPx = (tNorm / 100.0) * hPx;
    // 仅保留 0.04~0.06px 极微抗锯齿封边，绝不视觉增粗原迹！
    const minSealSw = isCjk ? 0.06 : 0.04;
    const addSw = Math.max(minSealSw, targetPx - estRenderedPx);
    svgEl.classList.add('pg-boost');
    const clampedSw = Math.min(isCjk ? 0.22 : 0.28, addSw);
    pathEl.setAttribute('stroke', 'currentColor');
    pathEl.setAttribute('vector-effect', 'non-scaling-stroke');
    pathEl.style.setProperty('stroke', 'currentColor', 'important');
    pathEl.style.setProperty('stroke-width', `${clampedSw.toFixed(2)}px`, 'important');
    pathEl.style.strokeLinejoin = 'round';
    pathEl.style.strokeLinecap = 'round';
    pathEl.style.paintOrder = 'stroke fill';
  }

  // 稳重自然的个人矢量字形渲染（自动笔宽均一化 + 汉字大字连笔节奏）
  function makePersonalSvg(ch, info, inMath, refFontPx) {
    const { cat, v } = info;
    const aspect = Math.max(0.14, v.w / Math.max(1, v.h));
    let hEm = 0.88, wEm = 0.6, vaEm = -0.06;

    switch (cat) {
      case 'cap':
        hEm = 0.96; wEm = Math.min(0.96, Math.max(0.24, hEm * aspect)); vaEm = -0.06; break;
      case 'asc':
        hEm = 1.00; wEm = Math.min(0.84, Math.max(0.24, hEm * aspect)); vaEm = -0.06; break;
      case 'tall':
        hEm = 1.24; wEm = Math.min(0.76, Math.max(0.32, hEm * aspect)); vaEm = -0.24; break;
      case 'xh':
        hEm = 0.74; wEm = Math.min(0.90, Math.max(0.24, hEm * aspect)); vaEm = -0.05; break;
      case 'desc':
        hEm = 1.00; wEm = Math.min(0.78, Math.max(0.32, hEm * aspect)); vaEm = -0.28; break;
      case 'delim':
        hEm = 1.15; wEm = Math.min(0.56, Math.max(0.22, hEm * aspect)); vaEm = -0.20; break;
      case 'op':
        hEm = 0.72;
        wEm = Math.min(0.94, Math.max(0.32, hEm * aspect));
        vaEm = 0.04; break;
      case 'op_wide':
        hEm = 0.72;
        wEm = Math.min(1.65, Math.max(1.02, hEm * aspect));
        vaEm = 0.02; break;
      case 'accent_bar':
        hEm = 0.26; wEm = 0.78; vaEm = 0.72; break;
      case 'accent_hat':
        hEm = 0.34; wEm = 0.66; vaEm = 0.68; break;
      case 'deg':
        hEm = 0.36; wEm = 0.32; vaEm = 0.44; break;
      case 'dots_h':
        hEm = 0.44; wEm = 0.92; vaEm = 0.04; break;
      case 'op_eq':
        wEm = Math.min(0.78, Math.max(0.52, 0.44 * aspect));
        hEm = Math.min(0.52, Math.max(0.30, wEm / aspect));
        vaEm = 0.14; break;
      case 'op_minus':
        wEm = Math.min(0.68, Math.max(0.44, 0.18 * aspect));
        hEm = Math.min(0.26, Math.max(0.12, wEm / aspect));
        vaEm = 0.24; break;
      case 'punct':
        hEm = 0.42; wEm = Math.min(0.34, Math.max(0.16, hEm * aspect)); vaEm = -0.14; break;
      case 'dot':
        hEm = 0.18; wEm = 0.18; vaEm = 0.24; break;
      case 'cjk':
        hEm = inMath ? 1.32 : 1.38;
        wEm = Math.min(1.42, Math.max(0.60, hEm * aspect));
        vaEm = -0.24; break;
      default:
        hEm = 0.88; wEm = Math.min(0.95, Math.max(0.28, hEm * aspect)); vaEm = -0.06;
    }

    if (!inMath && cat !== 'cjk') {
      hEm *= 0.94;
      wEm *= 0.94;
    }

    const isCjk = (cat === 'cjk');
    const isDelimBar = (ch === '|' || ch === '∣' || ch === '∥' || ch === '‖');
    const j = isCjk ? Math.min(1.15, TJ * 0.82) : (inMath ? MJ : Math.min(1.0, TJ * 0.7));
    const sx = 1.0 + randSym((isCjk ? 0.065 : (isDelimBar ? 0.08 : 0.045)) * j);
    const sy = 1.0 + randSym((isCjk ? 0.075 : (isDelimBar ? 0.10 : 0.055)) * j);
    const rot = randSym((isCjk ? 2.8 : (isDelimBar ? 3.2 : 1.6)) * j);
    const dy = randSym((isCjk ? 1.15 : (isDelimBar ? 1.35 : 0.50)) * j);
    const isOp = (cat === 'op' || cat === 'op_wide' || cat === 'op_eq' || cat === 'op_minus');
    const ml = isOp ? 0.13 : (isDelimBar ? 0.06 : (ch === 'Φ' ? 0.09 : (isCjk ? 0.025 : 0.015)));
    const mr = isOp ? 0.13 : (isDelimBar ? 0.06 : (ch === 'Φ' ? 0.12 : (isCjk ? (0.045 + randSym(0.028 * j)) : (0.025 + randSym(0.018 * j)))));

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', v.fill ? 'pg-svg' : 'pg-svg pg-stroke');
    svg.setAttribute('viewBox', `0 0 ${v.w} ${v.h}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.dataset.ch = ch;
    svg.dataset.cat = cat || '';
    svg.dataset.vidx = String(info.vidx ?? 0);
    svg.dataset.inMath = inMath ? '1' : '0';
    svg.style.width = `${wEm.toFixed(3)}em`;
    svg.style.height = `${hEm.toFixed(3)}em`;
    svg.style.verticalAlign = `${vaEm.toFixed(3)}em`;
    svg.style.marginLeft = `${ml.toFixed(3)}em`;
    svg.style.marginRight = `${Math.max(isCjk ? 0.005 : -0.02, mr).toFixed(3)}em`;
    svg.style.transform = `translateY(${dy.toFixed(2)}px) rotate(${rot.toFixed(2)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
    svg.style.opacity = Math.min(1.0, Math.max(0.95, 0.985 + randSym(0.018))).toFixed(2);

    const effFontPx = (refFontPx && refFontPx > 4) ? refFontPx : BASE_FONT_PX;
    const hPx = hEm * effFontPx;
    const targetPx = (effFontPx > 24.0)
      ? Math.min(1.82, effFontPx * 0.056)
      : (isCjk ? 0.76 : (effFontPx < 13.0 ? 0.64 : TARGET_STROKE_PX));

    const occ = (seenCount[ch] || 0);
    seenCount[ch] = occ + 1;
    const nVars = Math.max(1, info.nVars || 1);
    let effElasticAmp = 0;
    if (ELASTIC_AMP > 0 && !ELASTIC_EXEMPT_CATS.has(cat) && !ELASTIC_EXEMPT_CHARS.has(ch) && !isDelimBar && !isOp) {
      const varWeight = nVars === 1 ? 1.0 : (nVars === 2 ? 0.65 : (nVars === 3 ? 0.35 : 0.12));
      const occWeight = (occ === 0) ? 0.45 : 1.0;
      effElasticAmp = ELASTIC_AMP * varWeight * occWeight;
    }
    const elasticField = effElasticAmp > 0.002 ? makeElasticField(effElasticAmp) : null;

    // 如有克隆自 Andy 真迹的真实偏旁部首多边形（v7 偏旁克隆），先以实心细笔绘制
    if (v.d_fill) {
      const radFill = document.createElementNS(SVG_NS, 'path');
      radFill.setAttribute('class', 'pg-rad-fill');
      radFill.setAttribute('d', elasticField ? deformPathD(v.d_fill, v.w, v.h, elasticField) : v.d_fill);
      svg.appendChild(radFill);
    }

    // 如有顺势轻牵丝路径（连笔合成汉字），以轻盈半悬空笔压绘制
    if (v.d_hair) {
      const hair = document.createElementNS(SVG_NS, 'path');
      hair.setAttribute('d', elasticField ? deformPathD(v.d_hair, v.w, v.h, elasticField) : v.d_hair);
      hair.setAttribute('fill', 'none');
      hair.setAttribute('stroke', 'currentColor');
      hair.setAttribute('vector-effect', 'non-scaling-stroke');
      hair.style.setProperty('stroke', 'currentColor', 'important');
      hair.style.setProperty('stroke-width', `${(targetPx * 0.54).toFixed(2)}px`, 'important');
      hair.style.opacity = '0.52';
      hair.style.strokeLinecap = 'round';
      hair.style.strokeLinejoin = 'round';
      svg.appendChild(hair);
    }

    if (v.d) {
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', elasticField ? deformPathD(v.d, v.w, v.h, elasticField) : v.d);
      applyUniformInkCompensation(svg, path, v, hPx, targetPx, isCjk);
      svg.appendChild(path);
    }
    return svg;
  }

  const KATEX_MACROS = {
    "\\R": "\\mathbb{R}",
    "\\E": "\\mathrm{E}",
    "\\Var": "\\mathrm{Var}",
    "\\Cov": "\\mathrm{Cov}",
    "\\d": "\\mathrm{d}"
  };

  // 1. 离线渲染所有 KaTeX 数学公式
  document.querySelectorAll('.hw-math-display').forEach(el => {
    const tex = el.getAttribute('data-tex') || '';
    try {
      katex.render(tex, el, { displayMode: true, throwOnError: false, strict: false, macros: KATEX_MACROS });
    } catch (e) {
      el.textContent = tex;
    }
  });
  document.querySelectorAll('.hw-math-inline').forEach(el => {
    const tex = el.getAttribute('data-tex') || '';
    try {
      katex.render(tex, el, { displayMode: false, throwOnError: false, strict: false, macros: KATEX_MACROS });
    } catch (e) {
      el.textContent = tex;
    }
  });

  // 2. 用个人真实矢量笔迹替换 KaTeX 中的变量、数字、运算符与希腊字母
  if (PG) {
    document.querySelectorAll('.hw-answer .katex-html').forEach(khtml => {
      const leaves = khtml.querySelectorAll('span');
      leaves.forEach(sp => {
        if (sp.children.length > 0) return;
        if (
          sp.classList.contains('strut') ||
          sp.classList.contains('pstrut') ||
          sp.classList.contains('mspace') ||
          sp.classList.contains('frac-line') ||
          sp.classList.contains('overline-line') ||
          sp.classList.contains('op-symbol') ||
          sp.classList.contains('delimsizing') ||
          sp.closest('.delimsizing') ||
          sp.closest('.op-symbol') ||
          sp.closest('.accent-body') ||
          sp.closest('.hide-tail') ||
          sp.closest('.stretchy')
        ) return;

        const txt = sp.textContent;
        if (!txt || !txt.trim()) return;

        let hasHit = false;
        const katexCjkRe = /[\u4e00-\u9fff]/;
        for (const ch of txt) {
          if ((PG[ch] && (PG[ch].cat !== 'cjk' || USE_PERSONAL_CJK)) || katexCjkRe.test(ch)) {
            hasHit = true;
            break;
          }
        }
        if (!hasHit) return;

        const spFontPx = parseFloat(getComputedStyle(sp).fontSize) || BASE_FONT_PX;
        const frag = document.createDocumentFragment();
        for (const ch of txt) {
          const info = pickVariant(ch);
          if (info) {
            frag.appendChild(makePersonalSvg(ch, info, true, spFontPx));
          } else if (katexCjkRe.test(ch)) {
            const cjkSp = document.createElement('span');
            cjkSp.className = 'hw-ch hw-cjk hw-katex-cjk';
            cjkSp.textContent = ch;
            const rot = randSym(3.6 * TJ);
            const sx = CN_SX + randSym(0.09 * TJ);
            const sy = CN_SY + randSym(0.08 * TJ);
            const skew = CN_SKEW + randSym(2.6 * TJ);
            const dy = randSym(1.0 * TJ);
            const mr = Math.max(-2.0, CN_MR + randSym(0.80 * TJ));
            cjkSp.style.transform = `translateY(${dy.toFixed(2)}px) rotate(${rot.toFixed(2)}deg) skewX(${skew.toFixed(1)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
            cjkSp.style.marginRight = `${mr.toFixed(2)}px`;
            frag.appendChild(cjkSp);
          } else {
            const fb = document.createElement('span');
            fb.textContent = ch;
            fb.style.display = 'inline-block';
            fb.style.transform = `translateY(${randSym(0.5 * MJ).toFixed(2)}px) rotate(${randSym(1.4 * MJ).toFixed(2)}deg)`;
            frag.appendChild(fb);
          }
        }
        sp.textContent = '';
        sp.appendChild(frag);
      });
    });
  }

  // 3. v12 StarNote 压感丝带引擎（彻底消灭结构件的单线等宽电脑味：所有根号/大小中括号/分数线/上横线/长箭头全部采用 StarNote 压感墨带！）
  function sampleQuadBezier(x0, y0, cx, cy, x1, y1, stepPx) {
    const approx = Math.hypot(cx - x0, cy - y0) + Math.hypot(x1 - cx, y1 - cy);
    const n = Math.max(4, Math.ceil(approx / stepPx));
    const pts = [];
    for (let k = 0; k <= n; k++) {
      const t = k / n, mt = 1 - t;
      pts.push([mt * mt * x0 + 2 * mt * t * cx + t * t * x1, mt * mt * y0 + 2 * mt * t * cy + t * t * y1]);
    }
    return pts;
  }
  function sampleCubicBezier(x0, y0, c1x, c1y, c2x, c2y, x1, y1, stepPx) {
    const approx = Math.hypot(c1x - x0, c1y - y0) + Math.hypot(c2x - c1x, c2y - c1y) + Math.hypot(x1 - c2x, y1 - c2y);
    const n = Math.max(6, Math.ceil(approx / stepPx));
    const pts = [];
    for (let k = 0; k <= n; k++) {
      const t = k / n, mt = 1 - t;
      pts.push([
        mt*mt*mt*x0 + 3*mt*mt*t*c1x + 3*mt*t*t*c2x + t*t*t*x1,
        mt*mt*mt*y0 + 3*mt*mt*t*c1y + 3*mt*t*t*c2y + t*t*t*y1,
      ]);
    }
    return pts;
  }
  function strokePtsToStarNoteRibbon(rawPts, baseR, phase) {
    if (!rawPts || rawPts.length < 2) return '';
    const clean = [rawPts[0]];
    for (let i = 1; i < rawPts.length; i++) {
      if (Math.hypot(rawPts[i][0] - clean[clean.length - 1][0], rawPts[i][1] - clean[clean.length - 1][1]) > 0.35) {
        clean.push(rawPts[i]);
      }
    }
    const n = clean.length;
    if (n < 2) return '';
    const cum = [0];
    for (let i = 1; i < n; i++) {
      cum.push(cum[i - 1] + Math.hypot(clean[i][0] - clean[i - 1][0], clean[i][1] - clean[i - 1][1]));
    }
    const total = Math.max(1e-4, cum[n - 1]);
    const leftS = [], rightS = [];
    for (let i = 0; i < n; i++) {
      const u = cum[i] / total;
      const p0 = clean[Math.max(0, i - 1)];
      const p1 = clean[Math.min(n - 1, i + 1)];
      let tx = p1[0] - p0[0], ty = p1[1] - p0[1];
      const tl = Math.hypot(tx, ty) + 1e-6;
      tx /= tl; ty /= tl;
      const nx = -ty, ny = tx;
      let curv = 1.0;
      if (i > 0 && i < n - 1) {
        const v1x = clean[i][0] - clean[i - 1][0], v1y = clean[i][1] - clean[i - 1][1];
        const v2x = clean[i + 1][0] - clean[i][0], v2y = clean[i + 1][1] - clean[i][1];
        const cosA = (v1x * v2x + v1y * v2y) / ((Math.hypot(v1x, v1y) + 1e-6) * (Math.hypot(v2x, v2y) + 1e-6));
        if (cosA < 0.72) curv = 1.0 + 0.20 * (0.72 - cosA);
      }
      let taper = 1.0;
      if (u < 0.09) taper = 0.54 + 0.54 * (u / 0.09);
      else if (u > 0.80) taper = 1.0 - 0.68 * Math.pow((u - 0.80) / 0.20, 1.15);
      else taper = 0.96 + 0.08 * Math.sin(u * Math.PI);
      const dirF = 0.86 + 0.26 * Math.max(0, ty) - 0.10 * Math.max(0, -ty);
      const rip = 1.0 + 0.09 * Math.sin(u * 10.0 + phase);
      const r = Math.max(0.14, baseR * taper * dirF * curv * rip);
      leftS.push([clean[i][0] + nx * r, clean[i][1] + ny * r]);
      rightS.push([clean[i][0] - nx * r, clean[i][1] - ny * r]);
    }
    const poly = leftS.concat(rightS.reverse());
    const parts = [`M${poly[0][0].toFixed(2)} ${poly[0][1].toFixed(2)}`];
    for (let i = 1; i < poly.length; i++) parts.push(`L${poly[i][0].toFixed(2)} ${poly[i][1].toFixed(2)}`);
    parts.push('Z');
    return parts.join(' ');
  }

  // v14: 100% 调用 Andy 真实 StarNote 横线多边形（分数线 / 根号顶杠 / 上横线），按目标粗细等比锁高，零电脑味！
  function makeRealBarSvg(targetW, inkCol, targetStrokePx) {
    const tPx = targetStrokePx || 0.74;
    const w = Math.max(7.5, targetW);
    const key = (w <= 22.0) ? '__FRAC_SHORT__' : (w <= 56.0 ? '__FRAC_MED__' : '__FRAC_LONG__');
    const info = pickVariant(key) || pickVariant('__FRAC_MED__') || pickVariant('__FRAC_SHORT__');
    if (!info || !info.v || !info.v.d) return null;
    const v = info.v;
    const tNorm = Math.max(0.6, typeof v.t === 'number' ? v.t : 2.2);
    // 数学精确锁粗细：水平笔画竖向厚度 = v.t * (h / v.h) === tPx（无论 10px 短杠还是 160px 长分数线都恒定 0.74px！）
    const rawH = tPx * (v.h / tNorm);
    const h = Math.max(1.25, Math.min(6.2, rawH));

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
    svg.setAttribute('viewBox', `0 0 ${v.w} ${v.h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.dataset.ch = key;
    svg.dataset.cat = 'struct';
    svg.dataset.vidx = String(info.vidx ?? 0);
    if (inkCol) svg.style.color = inkCol;
    svg.style.width = `${w.toFixed(1)}px`;
    svg.style.height = `${h.toFixed(2)}px`;

    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', v.d);
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('vector-effect', 'non-scaling-stroke');
    path.style.setProperty('stroke', 'currentColor', 'important');
    path.style.setProperty('stroke-width', '0.04px', 'important');
    path.style.strokeLinejoin = 'round';
    path.style.strokeLinecap = 'round';
    path.style.paintOrder = 'stroke fill';
    svg.appendChild(path);
    return { svg, w, h };
  }

  function injectHandLine(el, strokeW) {
    const rect = el.getBoundingClientRect();
    const w = Math.max(8, rect.width);
    const pad = Math.min(2.6, Math.max(1.2, w * 0.04));
    const totalW = w + pad * 2;
    const inkCol = getComputedStyle(el).color;

    el.style.setProperty('border-bottom-color', 'transparent', 'important');
    el.style.setProperty('border-color', 'transparent', 'important');
    el.style.position = 'relative';

    const real = makeRealBarSvg(totalW, inkCol, strokeW || 0.74);
    if (real) {
      const { svg, h } = real;
      svg.style.left = `${(-pad + randSym(0.6)).toFixed(1)}px`;
      svg.style.top = `${(-h * 0.5 + randSym(0.45 * MJ)).toFixed(2)}px`;
      svg.style.transform = `rotate(${randSym(0.75 * MJ).toFixed(2)}deg)`;
      el.appendChild(svg);
      return real;
    }

    const h = 8;
    const y1 = 4.2 + randSym(0.75 * MJ);
    const y2 = 3.7 + randSym(0.75 * MJ);
    const ym = (y1 + y2) / 2 + randSym(0.60 * MJ);
    const xm = totalW * (0.4 + rand() * 0.2);
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
    svg.setAttribute('viewBox', `0 0 ${totalW.toFixed(1)} ${h}`);
    svg.style.width = `${totalW.toFixed(1)}px`;
    svg.style.height = `${h}px`;
    svg.style.left = `${(-pad).toFixed(1)}px`;
    svg.style.top = '-3.8px';

    const pts = sampleQuadBezier(0.6, y1, xm, ym, totalW - 0.6, y2, 1.8);
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', strokePtsToStarNoteRibbon(pts, strokeW * 0.54, rand() * 6.28));
    svg.appendChild(path);
    el.appendChild(svg);
    return { svg, w: totalW, h };
  }

  document.querySelectorAll('.hw-answer .katex .mfrac .frac-line').forEach(lineEl => {
    injectHandLine(lineEl, 0.74);
  });
  document.querySelectorAll('.hw-answer .katex .overline-line').forEach(lineEl => {
    const res = injectHandLine(lineEl, 0.72);
    if (res && res.svg) {
      res.svg.style.top = `${(-(res.h * 0.5 + 4.8 + rand() * 0.6)).toFixed(2)}px`;
    }
  });

  // 3b. v13.1 自然手写上横线/尖帽/波浪线/矢量箭头 \vec（直接包裹基字 SVG，彻底免疫 KaTeX vlist 偏移！）
  document.querySelectorAll('.hw-answer .katex .accent-body').forEach(ab => {
    const rawTxt = (ab.textContent || '').trim();
    const hasVecSvg = !!ab.querySelector('svg');
    if (!rawTxt && !hasVecSvg) return;
    let kind = 'bar';
    if (hasVecSvg || rawTxt.includes('\u20d7') || rawTxt.includes('→')) kind = 'vec';
    else if (rawTxt.includes('^') || rawTxt.includes('ˆ') || rawTxt.includes('∧')) kind = 'hat';
    else if (rawTxt.includes('~') || rawTxt.includes('˜')) kind = 'tilde';
    else if (rawTxt.includes('˙') || rawTxt.includes('.')) kind = 'dot';

    const accEl = ab.closest('.accent') || ab.closest('.vlist-t')?.parentElement || ab.parentElement;
    if (!accEl) return;

    ab.innerHTML = '';

    // 定位被加顶标的真实基字 SVG（如 X / μ）
    const allSvgs = Array.from(accEl.querySelectorAll('svg.pg-svg'));
    let baseEl = allSvgs.find(s => !s.closest('.accent-body'));
    if (!baseEl) {
      const spans = Array.from(accEl.querySelectorAll('span.mord, span.mathnormal, span.mathit'));
      baseEl = spans.find(s => !s.closest('.accent-body') && s.getBoundingClientRect().width > 2);
    }
    if (!baseEl || !baseEl.parentNode) return;

    const inkCol = getComputedStyle(accEl).color;
    // 用零行高 inline-block 包裹基字本身，使 top: 0 严格等于基字顶部边缘（绝不受 KaTeX vlist 干扰！）
    const wrap = document.createElement('span');
    wrap.className = 'hw-acc-wrap';
    wrap.style.display = 'inline-block';
    wrap.style.position = 'relative';
    wrap.style.lineHeight = '0';
    wrap.style.overflow = 'visible';
    baseEl.parentNode.insertBefore(wrap, baseEl);
    wrap.appendChild(baseEl);

    const wrapRect = wrap.getBoundingClientRect();
    const baseRect = baseEl.getBoundingClientRect();
    const bw = Math.max(7.5, baseRect.width);
    const leftBase = baseRect.left - wrapRect.left;
    const topBase = baseRect.top - wrapRect.top;

    if (kind === 'bar') {
      // v14: 直接采用 Andy 真实 StarNote 短横线 (__FRAC_SHORT__)，自然清瘦悬于基字上方 2.8~3.6px！
      const barW = Math.max(8.8, bw * (0.94 + rand() * 0.14));
      const realBar = makeRealBarSvg(barW, inkCol, 0.72);
      if (realBar) {
        const { svg, h: barH } = realBar;
        svg.style.left = `${(leftBase + (bw - barW) * 0.5 + randSym(0.5)).toFixed(1)}px`;
        const liftPx = (baseRect.height >= 12.0) ? (8.8 + barH * 0.5 + rand() * 0.6) : (3.5 + barH * 0.5 + rand() * 0.5);
        svg.style.top = `${(topBase - liftPx).toFixed(1)}px`;
        svg.style.transform = `rotate(${randSym(1.2).toFixed(2)}deg)`;
        wrap.appendChild(svg);
      }
    } else if (kind === 'hat') {
      const hw = Math.max(8.5, bw * 0.88);
      const hh = 6.0;
      const svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
      svg.setAttribute('viewBox', `0 0 ${hw.toFixed(1)} ${hh.toFixed(1)}`);
      svg.style.color = inkCol;
      svg.style.width = `${hw.toFixed(1)}px`;
      svg.style.height = `${hh.toFixed(1)}px`;
      svg.style.left = `${(leftBase + (bw - hw) * 0.5 + randSym(0.5)).toFixed(1)}px`;
      svg.style.top = `${(topBase - (6.8 + rand() * 0.8)).toFixed(1)}px`;
      const pts = sampleQuadBezier(0.8, 4.9, hw * 0.48, 0.8, hw * 0.52 + randSym(0.4), 1.2, 1.2)
        .concat(sampleQuadBezier(hw * 0.52 + randSym(0.4), 1.2, hw * 0.76, 3.0, hw - 0.8, 4.8, 1.2));
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', strokePtsToStarNoteRibbon(pts, 0.39, rand() * 6.28));
      svg.appendChild(p);
      wrap.appendChild(svg);
    } else if (kind === 'vec') {
      const vw = Math.max(9.5, bw * (0.96 + rand() * 0.14));
      const vh = 6.5;
      const svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
      svg.dataset.cat = 'struct';
      svg.dataset.ch = 'vec_arrow';
      svg.dataset.inMath = '1';
      svg.setAttribute('viewBox', `0 0 ${vw.toFixed(1)} ${vh.toFixed(1)}`);
      svg.style.color = inkCol;
      svg.style.width = `${vw.toFixed(1)}px`;
      svg.style.height = `${vh.toFixed(1)}px`;
      svg.style.left = `${(leftBase + (bw - vw) * 0.5 + randSym(0.5)).toFixed(1)}px`;
      const liftPx = (baseRect.height >= 12.0) ? (9.2 + rand() * 0.7) : (5.4 + rand() * 0.6);
      svg.style.top = `${(topBase - liftPx).toFixed(1)}px`;
      svg.style.transform = `rotate(${randSym(1.4).toFixed(2)}deg)`;
      const yMid = 3.7 + randSym(0.25);
      const shaft = sampleQuadBezier(0.6, yMid + 0.3, vw * 0.5, yMid - 0.35, vw - 1.0, yMid - 0.15, 1.3);
      const head = [
        [vw - 3.6, yMid - 2.3],
        [vw - 1.0, yMid - 0.15],
        [vw - 3.1, yMid + 1.8],
      ];
      const p1 = document.createElementNS(SVG_NS, 'path');
      p1.setAttribute('d', strokePtsToStarNoteRibbon(shaft, 0.36, rand() * 6.28) + ' ' + strokePtsToStarNoteRibbon(head, 0.33, rand() * 6.28));
      svg.appendChild(p1);
      wrap.appendChild(svg);
    } else if (kind === 'tilde') {
      const tw = Math.max(9.5, bw * 0.94);
      const th = 6.0;
      const svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
      svg.setAttribute('viewBox', `0 0 ${tw.toFixed(1)} ${th.toFixed(1)}`);
      svg.style.color = inkCol;
      svg.style.width = `${tw.toFixed(1)}px`;
      svg.style.height = `${th.toFixed(1)}px`;
      svg.style.left = `${(leftBase + (bw - tw) * 0.5 + randSym(0.5)).toFixed(1)}px`;
      svg.style.top = `${(topBase - (6.4 + rand() * 0.8)).toFixed(1)}px`;
      const pts = sampleQuadBezier(0.8, 4.1, tw * 0.28, 1.2, tw * 0.52, 3.1, 1.2)
        .concat(sampleQuadBezier(tw * 0.52, 3.1, tw * 0.76, 4.9, tw - 0.8, 2.0, 1.2));
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', strokePtsToStarNoteRibbon(pts, 0.39, rand() * 6.28));
      svg.appendChild(p);
      wrap.appendChild(svg);
    }
  });

  // 3e. v14 100% 真迹手写根号 √：直接调用 Andy w6 Q3 真实 StarNote √ 对勾 (__SQRT_HOOK__) + Andy 真实 StarNote 顶杠 (__FRAC_*)，零贝塞尔公式！
  document.querySelectorAll('.hw-answer .katex .mord.sqrt').forEach(sqrtEl => {
    const ht = sqrtEl.querySelector('.hide-tail');
    const oldSvg = ht ? ht.querySelector('svg') : null;
    if (!ht || !oldSvg) return;

    oldSvg.style.opacity = '0';
    if (sqrtEl.closest('.mfrac')) {
      sqrtEl.style.paddingTop = '5.2px';
    }
    sqrtEl.style.display = 'inline-block';
    sqrtEl.style.position = 'relative';
    sqrtEl.style.overflow = 'visible';

    const sRect = sqrtEl.getBoundingClientRect();
    const inkCol = getComputedStyle(sqrtEl).color;

    let radL = Infinity, radR = -Infinity, radT = Infinity, radB = -Infinity;
    sqrtEl.querySelectorAll('svg.pg-svg, svg.hw-fill-svg').forEach(sv => {
      if (sv.closest('.hide-tail')) return;
      const rc = sv.getBoundingClientRect();
      if (rc.width > 0.5 && rc.height > 0.5) {
        if (rc.left < radL) radL = rc.left;
        if (rc.right > radR) radR = rc.right;
        if (rc.top < radT) radT = rc.top;
        if (rc.bottom > radB) radB = rc.bottom;
      }
    });
    if (!isFinite(radL)) {
      radL = sRect.left + 12.0; radR = sRect.right;
      radT = sRect.top + 3.0; radB = sRect.bottom - 1.0;
    }

    const rl = Math.max(9.5, Math.min(18.0, radL - sRect.left));
    const rr = Math.max(rl + 8.0, radR - sRect.left);
    const rtLocal = radT - sRect.top;
    const rbLocal = radB - sRect.top;
    const peakY = rtLocal - (3.0 + rand() * 0.8);
    const peakX = rl - 1.5 + randSym(0.5);

    const hookInfo = pickVariant('__SQRT_HOOK__');
    if (hookInfo && hookInfo.v && hookInfo.v.d) {
      const hv = hookInfo.v;
      const hookH = Math.max(13.0, (rbLocal + 1.4) - peakY);
      const hookW = Math.min(rl + 1.2, Math.max(8.5, hookH * (hv.w / Math.max(1, hv.h)) * 0.96));
      const hSvg = document.createElementNS(SVG_NS, 'svg');
      hSvg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
      hSvg.setAttribute('viewBox', `0 0 ${hv.w} ${hv.h}`);
      hSvg.setAttribute('preserveAspectRatio', 'none');
      hSvg.dataset.ch = '__SQRT_HOOK__';
      hSvg.dataset.cat = 'struct';
      hSvg.dataset.vidx = String(hookInfo.vidx ?? 0);
      hSvg.style.color = inkCol;
      hSvg.style.width = `${hookW.toFixed(1)}px`;
      hSvg.style.height = `${hookH.toFixed(1)}px`;
      hSvg.style.left = `${(peakX - hookW + 0.6).toFixed(1)}px`;
      hSvg.style.top = `${peakY.toFixed(1)}px`;
      hSvg.style.transform = `rotate(${randSym(1.8).toFixed(2)}deg)`;
      const hp = document.createElementNS(SVG_NS, 'path');
      hp.setAttribute('d', hv.d);
      applyUniformInkCompensation(hSvg, hp, hv, hookH, 0.74, false);
      hSvg.appendChild(hp);
      sqrtEl.appendChild(hSvg);

      // 接上 Andy 真实 StarNote 顶杠（自然两笔衔接，保留 Andy w6 Q3 原汁原味手写根号感！）
      const barW = Math.max(9.0, (rr - peakX) + 2.2 + randSym(2.0));
      const realBar = makeRealBarSvg(barW, inkCol, 0.74);
      if (realBar) {
        const { svg: bSvg, h: bH } = realBar;
        bSvg.style.left = `${(peakX - 1.2 + randSym(0.9)).toFixed(1)}px`;
        bSvg.style.top = `${(peakY - bH * 0.42 + randSym(0.6)).toFixed(1)}px`;
        bSvg.style.transform = `rotate(${randSym(1.3).toFixed(2)}deg)`;
        sqrtEl.appendChild(bSvg);
      }
    }
  });

  // 3f. v15 100% 真迹长箭头替代 xrightarrow（Andy 真实横杠 __FRAC_* + 真实箭头尖 __ARROW_HEAD_R__，零贝塞尔！）
  document.querySelectorAll('.hw-answer .katex .x-arrow .svg-align').forEach(xa => {
    const oldSvg = xa.querySelector('svg');
    if (!oldSvg || oldSvg.classList.contains('hw-hand-svg')) return;
    const ht = xa.querySelector('.hide-tail') || xa;
    ht.style.minWidth = '2.0em';
    const htRect = ht.getBoundingClientRect();
    const xaRect = xa.getBoundingClientRect();
    if (htRect.width < 10) return;
    const inkCol = getComputedStyle(xa).color;
    oldSvg.style.opacity = '0';
    xa.style.position = 'relative';

    const w = Math.max(22, htRect.width);
    const h = Math.max(11, htRect.height + 3);
    const baseLeft = htRect.left - xaRect.left;
    const baseTop = htRect.top - xaRect.top - 1.5;
    const cy = baseTop + h * 0.56;

    const realBar = makeRealBarSvg(w - 2.4, inkCol, 0.74);
    const headInfo = pickVariant('__ARROW_HEAD_R__');
    if (realBar && headInfo && headInfo.v && headInfo.v.d) {
      const { svg: bSvg, h: bH } = realBar;
      bSvg.style.left = `${(baseLeft + 0.8).toFixed(1)}px`;
      bSvg.style.top = `${(cy - bH * 0.5 + randSym(0.35)).toFixed(2)}px`;
      bSvg.style.transform = `rotate(${randSym(1.1).toFixed(2)}deg)`;
      xa.appendChild(bSvg);

      const hv = headInfo.v;
      const headH = 6.2 + rand() * 1.1;
      const headW = Math.max(3.6, headH * (hv.w / Math.max(1, hv.h)));
      const hSvg = document.createElementNS(SVG_NS, 'svg');
      hSvg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
      hSvg.setAttribute('viewBox', `0 0 ${hv.w} ${hv.h}`);
      hSvg.setAttribute('preserveAspectRatio', 'none');
      hSvg.dataset.ch = '__ARROW_HEAD_R__';
      hSvg.dataset.cat = 'struct';
      hSvg.dataset.vidx = String(headInfo.vidx ?? 0);
      hSvg.style.color = inkCol;
      hSvg.style.width = `${headW.toFixed(1)}px`;
      hSvg.style.height = `${headH.toFixed(1)}px`;
      hSvg.style.left = `${(baseLeft + w - headW - 0.3 + randSym(0.5)).toFixed(1)}px`;
      hSvg.style.top = `${(cy - headH * 0.52 + randSym(0.45)).toFixed(1)}px`;
      hSvg.style.transform = `rotate(${randSym(2.0).toFixed(2)}deg)`;
      const hp = document.createElementNS(SVG_NS, 'path');
      hp.setAttribute('d', hv.d);
      applyUniformInkCompensation(hSvg, hp, hv, headH, 0.74, false);
      hSvg.appendChild(hp);
      xa.appendChild(hSvg);
      return;
    }

    const cyLocal = h * 0.54;
    const yStart = cyLocal + 0.7 + randSym(0.5);
    const yEnd = cyLocal - 0.4 + randSym(0.5);
    const yMid = (yStart + yEnd) * 0.5 + randSym(0.6);
    const headLenU = 3.5 + rand() * 1.0;
    const headLenD = 4.0 + rand() * 1.1;

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
    svg.setAttribute('viewBox', `0 0 ${w.toFixed(1)} ${h.toFixed(1)}`);
    svg.style.color = inkCol;
    svg.style.width = `${w.toFixed(1)}px`;
    svg.style.height = `${h.toFixed(1)}px`;
    svg.style.left = `${baseLeft.toFixed(1)}px`;
    svg.style.top = `${baseTop.toFixed(1)}px`;

    const shaft = sampleQuadBezier(1.0, yStart, w * 0.48, yMid, w - 1.1, yEnd, 1.6);
    const head = sampleQuadBezier(w - headLenU, yEnd - 2.3 - rand() * 0.6, w - headLenU * 0.42, yEnd - 0.9, w - 0.9, yEnd, 1.2)
      .concat(sampleQuadBezier(w - 0.9, yEnd, w - headLenD * 0.48, yEnd + 1.1, w - headLenD, yEnd + 2.6 + rand() * 0.6, 1.2));
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', strokePtsToStarNoteRibbon(shaft, 0.39, rand() * 6.28) + ' ' + strokePtsToStarNoteRibbon(head, 0.37, rand() * 6.28));
    svg.appendChild(p);
    xa.appendChild(svg);
  });

  document.querySelectorAll('.hw-answer .katex .mfrac').forEach(frac => {
    const vlist = frac.querySelector('.vlist');
    if (!vlist) return;
    Array.from(vlist.children).forEach(part => {
      if (part.querySelector('.frac-line')) return;
      const dx = randSym(1.2 * MJ);
      const rot = randSym(0.85 * MJ);
      part.style.transform = `translateX(${dx.toFixed(2)}px) rotate(${rot.toFixed(2)}deg)`;
    });
  });

  if (PG) {
    document.querySelectorAll('.hw-answer .katex .op-symbol.large-op, .hw-answer .katex .op-symbol.small-op').forEach(opEl => {
      const ch = (opEl.textContent || '').trim();
      let key = null;
      if (ch === '∮') key = PG['∮'] ? '∮' : '∫';
      else if (ch === '∬') key = PG['∬'] ? '∬' : '∫';
      else if (ch === '∭') key = PG['∭'] ? '∭' : '∫';
      else if (ch === '∫') key = '∫';
      else if (ch === '∑') key = '∑';
      else if (ch === '∏') key = '∏';
      if (!key || !PG[key]) return;
      const info = pickVariant(key);
      if (!info) return;

      const inkCol = getComputedStyle(opEl).color;
      const rect = opEl.getBoundingClientRect();
      const isSmallOp = opEl.classList.contains('small-op');
      const hMul = (key === '∫') ? 1.12 : (isSmallOp ? 1.00 : 1.06);
      const targetH = Math.max(16, rect.height * hMul);
      const rawAspect = info.v.w / Math.max(1, info.v.h);
      const aspect = (key === '∑') ? Math.min(0.95, rawAspect) : rawAspect;
      const targetW = Math.max(10, targetH * aspect);
      const extraSide = Math.max(0, (targetW - rect.width) / 2 + 1.8);
      if (extraSide > 0.6) {
        opEl.style.marginLeft = `${extraSide.toFixed(1)}px`;
        opEl.style.marginRight = `${(extraSide * 0.75).toFixed(1)}px`;
      }

      // 彻底移除原 KaTeX 印刷文本并锁定盒宽高，避免 PDF 嵌入 KaTeX_Size2 字体！
      opEl.style.display = 'inline-block';
      opEl.style.width = `${rect.width.toFixed(1)}px`;
      opEl.style.height = `${rect.height.toFixed(1)}px`;
      opEl.style.verticalAlign = 'middle';
      opEl.style.position = 'relative';
      opEl.textContent = '';

      const svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('class', info.v.fill ? 'hw-hand-svg hw-fill-svg' : 'hw-hand-svg');
      svg.setAttribute('viewBox', `0 0 ${info.v.w} ${info.v.h}`);
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.dataset.ch = key;
      svg.dataset.cat = info.cat || 'bigop';
      svg.dataset.vidx = String(info.vidx ?? 0);
      svg.style.color = inkCol;
      svg.style.width = `${targetW.toFixed(1)}px`;
      svg.style.height = `${targetH.toFixed(1)}px`;
      svg.style.left = `${((rect.width - targetW) / 2 + randSym(0.5 * MJ)).toFixed(1)}px`;
      svg.style.top = `${((rect.height - targetH) / 2 + randSym(0.5 * MJ)).toFixed(1)}px`;
      svg.style.transform = `rotate(${randSym(2.2 * MJ).toFixed(2)}deg)`;

      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', info.v.d);
      applyUniformInkCompensation(svg, path, info.v, targetH, 0.78, false);
      svg.appendChild(path);
      opEl.appendChild(svg);
    });
  }

  function buildHandDelimSvg(kind, w, h, inkCol) {
    // v15: 高圆括号 / 高大括号 / 高中括号全部直接调用 Andy 100% 真迹 StarNote 矢量多边形（零贝塞尔电脑味！）
    const realKeyMap = {
      '(': '__TALL_LPAREN__',
      ')': '__TALL_RPAREN__',
      '{': '__TALL_LBRACE__',
      '}': '__TALL_RBRACE__',
      '[': '__TALL_LBRACK__',
      ']': '__TALL_RBRACK__',
    };
    const rKey = realKeyMap[kind];
    if (rKey && PG && PG[rKey] && PG[rKey].v && PG[rKey].v.length > 0) {
      const info = pickVariant(rKey);
      if (info && info.v && info.v.d) {
        const v = info.v;
        const isParen = (kind === '(' || kind === ')');
        const sh = Math.max(h, 16) * (isParen ? (1.02 + rand() * 0.05) : (1.07 + rand() * 0.06));
        const asp = Math.max(0.18, Math.min(0.46, v.w / Math.max(1, v.h)));
        const sw = isParen
          ? Math.max(5.8, Math.min(11.5, sh * asp * 1.02))
          : Math.max(7.2, Math.min(13.5, sh * asp * 1.06));
        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
        svg.setAttribute('viewBox', `0 0 ${v.w} ${v.h}`);
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.dataset.ch = rKey;
        svg.dataset.cat = 'struct';
        svg.dataset.vidx = String(info.vidx ?? 0);
        svg.style.color = inkCol;
        svg.style.width = `${sw.toFixed(1)}px`;
        svg.style.height = `${sh.toFixed(1)}px`;
        svg.style.left = `${((w - sw) * 0.5 + randSym(0.6)).toFixed(1)}px`;
        svg.style.top = `${((h - sh) * 0.5 + randSym(0.8)).toFixed(1)}px`;
        svg.style.transform = `rotate(${randSym(1.8 * MJ).toFixed(2)}deg)`;
        const p = document.createElementNS(SVG_NS, 'path');
        p.setAttribute('d', v.d);
        applyUniformInkCompensation(svg, p, v, sh, 0.74, false);
        svg.appendChild(p);
        return svg;
      }
    }

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
    const padX = 4.0;
    const sw = Math.max(w, 10) + padX * 2;
    const sh = Math.max(h, 14);
    svg.setAttribute('viewBox', `0 0 ${sw.toFixed(1)} ${sh.toFixed(1)}`);
    svg.style.color = inkCol;
    svg.style.width = `${sw.toFixed(1)}px`;
    svg.style.height = `${sh.toFixed(1)}px`;
    svg.style.left = `${(-padX).toFixed(1)}px`;
    svg.style.top = '0px';
    svg.style.transform = `rotate(${randSym(1.8 * MJ).toFixed(2)}deg)`;

    const jx = (a) => randSym(a * Math.max(0.78, MJ));
    const jy = (a) => randSym(a * Math.max(0.78, MJ));
    const cx = sw / 2;
    const yTop = 1.6 + rand() * Math.min(3.4, sh * 0.08);
    const yBot = sh - 1.6 - rand() * Math.min(3.4, sh * 0.08);
    const yMid = yTop + (yBot - yTop) * (0.46 + rand() * 0.08);
    const slant = Math.min(2.6, sh * 0.048);
    const rPen = 0.40 + randSym(0.03);
    let dRibbon = '';

    if (kind === '(') {
      const xTop = cx + 2.1 + slant * 0.4 + jx(0.8);
      const xBot = cx + 1.5 - slant * 0.4 + jx(0.9);
      const c1x = cx - 4.0 + jx(0.9);
      const c2x = cx - 4.3 + jx(0.9);
      const pts = sampleCubicBezier(xTop, yTop, c1x, yTop + (yBot - yTop) * (0.25 + rand() * 0.07), c2x, yTop + (yBot - yTop) * (0.69 + rand() * 0.07), xBot, yBot, 1.6);
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === ')') {
      const xTop = cx - 1.6 + slant * 0.4 + jx(0.8);
      const xBot = cx - 2.3 - slant * 0.4 + jx(0.9);
      const c1x = cx + 4.2 + jx(0.9);
      const c2x = cx + 3.9 + jx(0.9);
      const pts = sampleCubicBezier(xTop, yTop, c1x, yTop + (yBot - yTop) * (0.26 + rand() * 0.07), c2x, yTop + (yBot - yTop) * (0.70 + rand() * 0.07), xBot, yBot, 1.6);
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === '[') {
      // 手写中括号：自然轻右倾 + 随手圆钝转角 + 略微弓弯竖杆（绝不画 90° 僵硬方框！）
      const xTopR = cx + 3.2 + slant * 0.5 + jx(0.7);
      const xTopL = cx - 1.0 + slant * 0.5 + jx(0.6);
      const xBotL = cx - 1.4 - slant * 0.5 + jx(0.6);
      const xBotR = cx + 2.4 - slant * 0.5 + jx(0.8);
      const pts = []
        .concat(sampleQuadBezier(xTopR, yTop + jy(0.5), xTopL + 0.6, yTop + 0.2, xTopL, yTop + Math.min(3.5, sh * 0.10), 1.3))
        .concat(sampleQuadBezier(xTopL, yTop + Math.min(3.5, sh * 0.10), (xTopL + xBotL) * 0.5 + jx(0.9), yMid, xBotL, yBot - Math.min(3.2, sh * 0.10), 1.6))
        .concat(sampleQuadBezier(xBotL, yBot - Math.min(3.2, sh * 0.10), xBotL + 0.4, yBot + 0.3, xBotR, yBot + jy(0.6), 1.3));
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === ']') {
      const xTopL = cx - 2.8 + slant * 0.5 + jx(0.7);
      const xTopR = cx + 1.2 + slant * 0.5 + jx(0.6);
      const xBotR = cx + 0.8 - slant * 0.5 + jx(0.6);
      const xBotL = cx - 2.6 - slant * 0.5 + jx(0.8);
      const pts = []
        .concat(sampleQuadBezier(xTopL, yTop + jy(0.5), xTopR - 0.5, yTop + 0.2, xTopR, yTop + Math.min(3.5, sh * 0.10), 1.3))
        .concat(sampleQuadBezier(xTopR, yTop + Math.min(3.5, sh * 0.10), (xTopR + xBotR) * 0.5 + jx(0.9), yMid, xBotR, yBot - Math.min(3.2, sh * 0.10), 1.6))
        .concat(sampleQuadBezier(xBotR, yBot - Math.min(3.2, sh * 0.10), xBotR - 0.4, yBot + 0.3, xBotL, yBot + jy(0.6), 1.3));
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === '{') {
      // v13 复刻 Andy w6 Q3 真迹分段大括号 {：清晰圆润顶钩 -> 近竖上臂 -> 清晰柔润左尖 -> 近竖下臂 -> 圆润底钩（绝不成 < 形！）
      const my = yTop + (yBot - yTop) * (0.46 + rand() * 0.08);
      const uh = my - yTop;
      const lh = yBot - my;
      const xHook1 = cx + 3.4 + jx(0.5);
      const xStem1 = cx - 0.2 + jx(0.4);
      const xTip   = cx - 4.0 + jx(0.5);
      const xStem2 = cx - 0.5 + jx(0.4);
      const xHook2 = cx + 3.0 + jx(0.5);
      const yCurve1 = yTop + Math.min(7.5, uh * 0.34);
      const yKnee1  = my - Math.min(6.5, uh * 0.30);
      const yKnee2  = my + Math.min(6.5, lh * 0.30);
      const yCurve2 = yBot - Math.min(7.5, lh * 0.34);
      const pts = []
        // 1) 顶钩柔弯入上竖臂
        .concat(sampleQuadBezier(xHook1, yTop, xStem1 + 0.2, yTop + 0.4, xStem1, yCurve1, 1.3))
        // 2) 上竖臂微弓下行至中弯
        .concat(sampleQuadBezier(xStem1, yCurve1, xStem1 + jx(0.5), (yCurve1 + yKnee1) * 0.5, xStem1 - 0.3, yKnee1, 1.5))
        // 3) 柔润左向中尖（清晰但不僵硬）
        .concat(sampleQuadBezier(xStem1 - 0.3, yKnee1, xStem1 - 0.8, my - 1.4, xTip, my, 1.2))
        .concat(sampleQuadBezier(xTip, my, xStem2 - 0.8, my + 1.4, xStem2 - 0.2, yKnee2, 1.2))
        // 4) 下竖臂微弓下行 + 底钩柔弯收尾
        .concat(sampleQuadBezier(xStem2 - 0.2, yKnee2, xStem2 + jx(0.5), (yKnee2 + yCurve2) * 0.5, xStem2, yCurve2, 1.5))
        .concat(sampleQuadBezier(xStem2, yCurve2, xStem2 + 0.2, yBot - 0.4, xHook2, yBot, 1.3));
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === '}') {
      const my = yTop + (yBot - yTop) * (0.46 + rand() * 0.08);
      const uh = my - yTop;
      const lh = yBot - my;
      const xHook1 = cx - 3.4 + jx(0.5);
      const xStem1 = cx + 0.2 + jx(0.4);
      const xTip   = cx + 4.0 + jx(0.5);
      const xStem2 = cx + 0.5 + jx(0.4);
      const xHook2 = cx - 3.0 + jx(0.5);
      const yCurve1 = yTop + Math.min(7.5, uh * 0.34);
      const yKnee1  = my - Math.min(6.5, uh * 0.30);
      const yKnee2  = my + Math.min(6.5, lh * 0.30);
      const yCurve2 = yBot - Math.min(7.5, lh * 0.34);
      const pts = []
        .concat(sampleQuadBezier(xHook1, yTop, xStem1 - 0.2, yTop + 0.4, xStem1, yCurve1, 1.3))
        .concat(sampleQuadBezier(xStem1, yCurve1, xStem1 + jx(0.5), (yCurve1 + yKnee1) * 0.5, xStem1 + 0.3, yKnee1, 1.5))
        .concat(sampleQuadBezier(xStem1 + 0.3, yKnee1, xStem1 + 0.8, my - 1.4, xTip, my, 1.2))
        .concat(sampleQuadBezier(xTip, my, xStem2 + 0.8, my + 1.4, xStem2 + 0.2, yKnee2, 1.2))
        .concat(sampleQuadBezier(xStem2 + 0.2, yKnee2, xStem2 + jx(0.5), (yKnee2 + yCurve2) * 0.5, xStem2, yCurve2, 1.5))
        .concat(sampleQuadBezier(xStem2, yCurve2, xStem2 - 0.2, yBot - 0.4, xHook2, yBot, 1.3));
      dRibbon = strokePtsToStarNoteRibbon(pts, rPen, rand() * 6.28);
    } else if (kind === '||') {
      const lean1 = Math.min(2.8, sh * (0.045 + rand() * 0.03));
      const lean2 = Math.min(2.8, sh * (0.040 + rand() * 0.03));
      const x1 = cx - 1.8, x2 = cx + 1.8;
      const yt2 = 1.6 + rand() * Math.min(3.6, sh * 0.09);
      const yb2 = sh - 1.6 - rand() * Math.min(3.6, sh * 0.09);
      const p1 = sampleQuadBezier(x1 + lean1 * 0.55 + jx(0.6), yTop, x1 + jx(1.1), yMid, x1 - lean1 * 0.55 + jx(0.6), yBot, 1.6);
      const p2 = sampleQuadBezier(x2 + lean2 * 0.55 + jx(0.6), yt2, x2 + jx(1.1), (yt2 + yb2) * 0.5, x2 - lean2 * 0.55 + jx(0.6), yb2, 1.6);
      dRibbon = strokePtsToStarNoteRibbon(p1, rPen, rand() * 6.28) + ' ' + strokePtsToStarNoteRibbon(p2, rPen, rand() * 6.28);
    } else {
      const lean = Math.min(3.2, sh * (0.05 + rand() * 0.035));
      const bow = (rand() < 0.5 ? -1 : 1) * (0.5 + rand() * 1.0);
      const p1 = sampleQuadBezier(cx + lean * 0.55 + jx(0.7), yTop, cx + bow + jx(0.8), yMid, cx - lean * 0.55 + jx(0.7), yBot, 1.6);
      dRibbon = strokePtsToStarNoteRibbon(p1, rPen, rand() * 6.28);
    }

    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', dRibbon);
    svg.appendChild(p);
    return svg;
  }

  document.querySelectorAll('.hw-answer .katex .delimsizing').forEach(dl => {
    const txt = (dl.textContent || '').trim();
    const dlSvg = dl.querySelector('svg');
    let kind = null;
    if (txt.includes('(') || txt.includes('⎛') || txt.includes('⎝')) kind = '(';
    else if (txt.includes(')') || txt.includes('⎞') || txt.includes('⎠')) kind = ')';
    else if (txt.includes('[') || txt.includes('⎡') || txt.includes('⎣')) kind = '[';
    else if (txt.includes(']') || txt.includes('⎤') || txt.includes('⎦')) kind = ']';
    else if (txt.includes('{') || txt.includes('⎧') || txt.includes('⎨')) kind = '{';
    else if (txt.includes('}') || txt.includes('⎫') || txt.includes('⎬')) kind = '}';
    else if (txt.includes('‖') || txt.includes('∥')) kind = '||';
    else if (txt.includes('|') || txt.includes('∣') || txt.includes('│') || txt.includes('⎪') || txt.includes('⏐')) kind = '|';
    else if (dlSvg && !dlSvg.classList.contains('hw-hand-svg')) {
      const sRect = dlSvg.getBoundingClientRect();
      const pathD = (dlSvg.querySelector('path')?.getAttribute('d') || '');
      const vb = (dlSvg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
      if (vb.length === 4 && vb[2] >= 500 && vb[2] <= 720 && pathD.includes('v585')) {
        kind = '||';
      } else if ((sRect.width <= 11 && sRect.height >= 12) || pathD.includes('H145') || pathD.includes('v585')) {
        kind = '|';
      }
    }
    if (!kind) return;

    const inkCol = getComputedStyle(dl).color;
    const dlRect = dl.getBoundingClientRect();
    const tgtRect = (dlSvg && !dlSvg.classList.contains('hw-hand-svg')) ? dlSvg.getBoundingClientRect() : dlRect;
    if (tgtRect.height < 10) return;

    const offLeft = (dlSvg && !dlSvg.classList.contains('hw-hand-svg')) ? (tgtRect.left - dlRect.left) : 0;
    const offTop = (dlSvg && !dlSvg.classList.contains('hw-hand-svg')) ? (tgtRect.top - dlRect.top) : 0;

    // 锁定原占位框并清空印刷文本节点，避免 PDF 嵌入 KaTeX_Size3/Size4 字体！
    dl.style.position = 'relative';
    dl.style.display = 'inline-block';
    dl.style.width = `${dlRect.width.toFixed(1)}px`;
    dl.style.height = `${dlRect.height.toFixed(1)}px`;
    dl.style.verticalAlign = 'middle';
    dl.textContent = '';
    if (kind === '{') {
      dl.style.marginRight = '5.5px';
      dl.style.marginLeft = '1.5px';
    } else if (kind === '}') {
      dl.style.marginLeft = '4.5px';
    }

    const handSvg = buildHandDelimSvg(kind, tgtRect.width, tgtRect.height, inkCol);
    handSvg.style.left = `${(offLeft - 4.0).toFixed(1)}px`;
    handSvg.style.top = `${offTop.toFixed(1)}px`;
    dl.appendChild(handSvg);
  });

  document.querySelectorAll('.hw-answer .katex .mopen, .hw-answer .katex .mclose').forEach(mc => {
    if (mc.querySelector('.delimsizing') || mc.querySelector('.hw-hand-svg')) return;
    const innerSvg = mc.querySelector('svg');
    if (!innerSvg || innerSvg.classList.contains('pg-svg')) return;
    const inkCol = getComputedStyle(mc).color;
    const rect = innerSvg.getBoundingClientRect();
    if (rect.height < 16) return;
    const pathD = (innerSvg.querySelector('path')?.getAttribute('d') || '');
    let kind = mc.classList.contains('mopen') ? '(' : ')';
    if (rect.width <= 5.5 && rect.height >= 16) kind = '|';
    else if (mc.closest('.mtable') || mc.parentElement?.querySelector('.mtable')) kind = '{';
    else if (pathD.includes('c-')) kind = mc.classList.contains('mopen') ? '{' : '}';
    innerSvg.style.opacity = '0';
    mc.style.position = 'relative';
    if (kind === '{') {
      mc.style.marginRight = '6.0px';
      mc.style.marginLeft = '1.5px';
    } else if (kind === '}') {
      mc.style.marginLeft = '4.5px';
    }
    mc.appendChild(buildHandDelimSvg(kind, Math.max(rect.width, 6), rect.height, inkCol));
  });

  // 3d. 积分/求和上下限轻度自然偏置
  document.querySelectorAll('.hw-answer .katex .msupsub').forEach(ms => {
    const dx = randSym(0.9 * MJ);
    const dy = randSym(0.8 * MJ);
    const rot = randSym(1.1 * MJ);
    ms.style.display = ms.style.display || 'inline-block';
    ms.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${rot.toFixed(1)}deg)`;
  });

  // 3g. 分段函数 cases 自然紧贴（修复 "0, else" 强行与上一行右列对齐的表格感！）
  document.querySelectorAll('.hw-answer .katex .mtable').forEach(mt => {
    const cols = Array.from(mt.children).filter(el => el.classList.contains('col-align-l') || el.classList.contains('col-align-r') || el.classList.contains('col-align-c'));
    if (cols.length < 2) return;
    // 仅针对左对齐分段函数（前面是大括号或 2 列 cases），不动标准矩阵
    const parentHtml = mt.closest('.minner, .mord');
    const isCases = (cols.length === 2 && cols[0].classList.contains('col-align-l') && cols[1].classList.contains('col-align-l'));
    if (!isCases && !(parentHtml && parentHtml.querySelector('.mopen'))) return;

    function getRows(colEl) {
      const vlist = colEl.querySelector('.vlist-t > .vlist-r > .vlist');
      if (!vlist) return [];
      return Array.from(vlist.children).filter(r => r.querySelector('.mord, svg'));
    }
    function getInkBounds(rowEl) {
      const items = rowEl.querySelectorAll('svg.pg-svg, svg.hw-hand-svg, span.hw-ch');
      let minL = Infinity, maxR = -Infinity;
      items.forEach(it => {
        const rc = it.getBoundingClientRect();
        if (rc.width > 0.5 && rc.height > 0.5) {
          if (rc.left < minL) minL = rc.left;
          if (rc.right > maxR) maxR = rc.right;
        }
      });
      if (!isFinite(minL)) {
        const rc = rowEl.getBoundingClientRect();
        return { left: rc.left, right: rc.right };
      }
      return { left: minL, right: maxR };
    }

    const rows0 = getRows(cols[0]);
    const rows1 = getRows(cols[1]);
    if (rows0.length >= 2 && rows0.length === rows1.length) {
      for (let r = 0; r < rows0.length; r++) {
        const b0 = getInkBounds(rows0[r]);
        const b1 = getInkBounds(rows1[r]);
        const gap = b1.left - b0.right;
        const wantGap = 8.5 + rand() * 7.5;
        const rowDy = randSym(1.4 * MJ);
        const rowDx0 = randSym(2.8 * MJ);
        const rowRot = randSym(1.0 * MJ);
        rows0[r].style.transform = `translate(${rowDx0.toFixed(1)}px, ${rowDy.toFixed(1)}px) rotate(${rowRot.toFixed(2)}deg)`;
        let shiftX = rowDx0 + randSym(2.5 * MJ);
        if (gap > wantGap + 4.5) {
          shiftX = -(gap - wantGap) + rowDx0;
        }
        rows1[r].style.transform = `translate(${shiftX.toFixed(1)}px, ${(rowDy + randSym(1.1 * MJ)).toFixed(1)}px) rotate(${randSym(1.1 * MJ).toFixed(2)}deg)`;
      }
    }
  });

  // 4. Opt #3 右边距渐进挤写与轻度下垂（>74% 宽度时尾部自然收窄略下垂，再兜底防溢出）
  function applyRightMarginSqueeze(katexNode, avail) {
    if (!katexNode || avail <= 80) return;
    let actual = katexNode.scrollWidth;
    if (actual > avail * 0.74) {
      const bases = Array.from(katexNode.querySelectorAll('.katex-html > .base'));
      const terms = [];
      bases.forEach(b => {
        Array.from(b.children).forEach(ch => {
          if (!ch.classList.contains('strut') && !ch.classList.contains('mspace')) terms.push(ch);
        });
      });
      const nT = terms.length;
      if (nT >= 5) {
        const startIdx = Math.floor(nT * 0.68);
        const tailN = Math.max(1, nT - startIdx);
        for (let k = startIdx; k < nT; k++) {
          const tEl = terms[k];
          if (tEl.querySelector('.mtable')) continue;
          const prog = (k - startIdx + 1) / tailN;
          const sx = 1.0 - prog * (actual > avail ? 0.14 : 0.08);
          const droopY = prog * (actual > avail ? 5.0 : 2.8) + randSym(0.6);
          tEl.style.display = tEl.style.display || 'inline-block';
          tEl.style.transformOrigin = 'left center';
          const prevT = tEl.style.transform || '';
          tEl.style.transform = `${prevT} translateY(${droopY.toFixed(1)}px) scaleX(${sx.toFixed(3)})`.trim();
        }
      }
      actual = katexNode.scrollWidth;
    }
    if (actual > avail) {
      const ratio = Math.max(0.88, (avail / actual) * 0.97);
      katexNode.style.display = 'inline-block';
      katexNode.style.transformOrigin = 'left center';
      katexNode.style.transform = `scale(${ratio.toFixed(3)})`;
    }
  }

  document.querySelectorAll('.hw-math-display').forEach(el => {
    const katexNode = el.querySelector('.katex-display > .katex') || el.querySelector('.katex');
    applyRightMarginSqueeze(katexNode, el.clientWidth - 18);
  });
  document.querySelectorAll('.hw-answer p').forEach(p => {
    p.querySelectorAll('.hw-math-inline .katex').forEach(inlineMath => {
      applyRightMarginSqueeze(inlineMath, p.clientWidth - 28);
    });
  });

  // 清除 KaTeX 内部不可见 PUA 占位符 (\uE020 等)，确保 PDF 答案层 visible_font_spans === 0
  document.querySelectorAll('.hw-answer .katex').forEach(kEl => {
    const w = document.createTreeWalker(kEl, NodeFilter.SHOW_TEXT, null);
    const puaNodes = [];
    while (w.nextNode()) {
      if (/[\uE000-\uF8FF]/.test(w.currentNode.nodeValue || '')) puaNodes.push(w.currentNode);
    }
    puaNodes.forEach(n => { n.nodeValue = (n.nodeValue || '').replace(/[\uE000-\uF8FF]/g, ''); });
  });

  // 5. 中文快写随性扰动（高自由度 TJ） + 公式稳重自然流（温和 MJ）
  if (TJ > 0.001 || MJ > 0.001) {
    const cjkIdeoRe = /[\u4e00-\u9fff]/;
    const cjkPunctRe = /[\u3000-\u303f\uff00-\uffef]/;
    const tokenRe = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]|\s+|[A-Za-z0-9]|./g;
    const wsRe = /^\s+$/;
    const noStartPunctRe = /^[，。、；：！？）》】\.,;:!\?]$/;

    function jitterProse(rootEl) {
      const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
          const p = node.parentElement;
          if (!p) return NodeFilter.FILTER_REJECT;
          if (p.closest('.katex') || p.closest('.ws-prompt') || p.closest('svg')) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });
      const textNodes = [];
      while (walker.nextNode()) textNodes.push(walker.currentNode);

      let baseline = 0;
      let charPos = 0;
      const wavePhase = rand() * Math.PI * 2;
      const waveFreq = 0.24 + rand() * 0.14;

      textNodes.forEach(tn => {
        const str = tn.nodeValue;
        const frag = document.createDocumentFragment();
        const parts = str.match(tokenRe) || [str];

        let startIdx = 0;
        const prevEl = tn.previousSibling;
        if (
          parts.length > 0 &&
          noStartPunctRe.test(parts[0]) &&
          prevEl &&
          prevEl.nodeType === 1 &&
          prevEl.classList &&
          prevEl.classList.contains('hw-math-inline')
        ) {
          const wrap = document.createElement('span');
          wrap.className = 'hw-nowrap';
          prevEl.parentNode.insertBefore(wrap, prevEl);
          wrap.appendChild(prevEl);
          const pInfo = pickVariant(parts[0]);
          if (pInfo) {
            const elFontPx = parseFloat(getComputedStyle(tn.parentElement).fontSize) || BASE_FONT_PX;
            wrap.appendChild(makePersonalSvg(parts[0], pInfo, false, elFontPx));
          } else {
            const psp = document.createElement('span');
            psp.className = 'hw-ch';
            psp.textContent = parts[0];
            wrap.appendChild(psp);
          }
          startIdx = 1;
        }

        let lastAppended = null;
        for (let idx = startIdx; idx < parts.length; idx++) {
          const ch = parts[idx];
          if (wsRe.test(ch)) {
            frag.appendChild(document.createTextNode(ch));
            lastAppended = null;
            continue;
          }
          charPos++;
          const pgInfo = pickVariant(ch);
          let nodeEl;
          if (pgInfo) {
            const elFontPx = parseFloat(getComputedStyle(tn.parentElement).fontSize) || BASE_FONT_PX;
            nodeEl = makePersonalSvg(ch, pgInfo, false, elFontPx);
            if (pgInfo.cat === 'cjk') {
              baseline = baseline * 0.78 + randSym(0.85 * TJ);
              const wave = Math.sin(charPos * waveFreq + wavePhase) * 1.25 * TJ;
              const prevT = nodeEl.style.transform || '';
              nodeEl.style.transform = `translateY(${(baseline + wave).toFixed(2)}px) ${prevT}`;
            }
          } else {
            const isCjkIdeo = cjkIdeoRe.test(ch);
            const isCjkPunct = !isCjkIdeo && cjkPunctRe.test(ch);
            // 中文采用连笔快写随性波动（大字舒展+紧字距+轻斜瘦长，打破小巧精致感）
            baseline = baseline * 0.80 + randSym(0.95 * TJ);
            const wave = Math.sin(charPos * waveFreq + wavePhase) * 1.45 * TJ;
            const dy = baseline + wave + randSym((isCjkIdeo ? 1.20 : 0.85) * TJ);
            const rot = randSym((isCjkIdeo ? 4.2 : 2.6) * TJ);
            const sx = (isCjkIdeo ? CN_SX : 0.94) + randSym((isCjkIdeo ? 0.10 : 0.06) * TJ);
            const sy = (isCjkIdeo ? CN_SY : 1.00) + randSym((isCjkIdeo ? 0.09 : 0.06) * TJ);
            const skew = (isCjkIdeo ? CN_SKEW : -2.5) + randSym((isCjkIdeo ? 2.8 : 2.0) * TJ);
            // 相邻汉字字距紧凑微粘连（下限 -2.0px 避免过分叠字），复刻连笔快写节奏
            const mrRaw = (isCjkIdeo ? CN_MR : (isCjkPunct ? -0.4 : -0.1)) + randSym((isCjkIdeo ? 0.80 : 0.6) * TJ);
            const mr = isCjkIdeo ? Math.max(-2.0, mrRaw) : mrRaw;
            const op = Math.min(1.0, Math.max(0.85, 0.94 + randSym(0.06 * TJ)));

            const sp = document.createElement('span');
            sp.className = isCjkIdeo ? 'hw-ch hw-cjk' : (isCjkPunct ? 'hw-ch hw-cjk-punct' : 'hw-ch');
            sp.textContent = ch;
            sp.style.transform = `translateY(${dy.toFixed(2)}px) rotate(${rot.toFixed(2)}deg) skewX(${skew.toFixed(1)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
            sp.style.marginRight = `${mr.toFixed(2)}px`;
            sp.style.opacity = op.toFixed(2);
            nodeEl = sp;
          }

          if (noStartPunctRe.test(ch) && lastAppended && lastAppended.parentNode === frag) {
            const nw = document.createElement('span');
            nw.className = 'hw-nowrap';
            frag.replaceChild(nw, lastAppended);
            nw.appendChild(lastAppended);
            nw.appendChild(nodeEl);
            lastAppended = nw;
          } else {
            frag.appendChild(nodeEl);
            lastAppended = nodeEl;
          }
        }
        tn.parentNode.replaceChild(frag, tn);
      });
    }

    // 网格/独立页眉（如红字 W10、标题、姓名学号）也统一走个人矢量笔迹，彻底告别第三方字体！
    document.querySelectorAll('.st-grid-tag, .st-grid-meta, .st-title, .st-meta').forEach(hdr => {
      jitterProse(hdr);
    });

    document.querySelectorAll('.hw-answer, .hw-text-line').forEach(block => {
      jitterProse(block);

      if (!GRID_LOCK && block.classList.contains('hw-answer')) {
        block.style.marginTop = `${(2 + rand() * 8 * TJ).toFixed(1)}px`;
      }

      // 复刻 StarNote 真人手写行级排版：阶梯式/参差左缩进 + 整行自然斜率（-1.45° ~ +1.25°）+ 忽紧忽松行距 + 行间字号呼吸感
      let leftDrift = (rand() < 0.38) ? (22 + rand() * 38 * TJ) : (4 + rand() * 18 * TJ);
      const blockBiasDeg = randSym(0.75 * Math.max(MJ, 0.55));
      let lineIdx = 0;
      block.querySelectorAll(':scope > p, :scope > .hw-math-display, li > .hw-math-display, :scope > ul, :scope > ol').forEach(line => {
        const rawTxt = (line.textContent || '').trim();
        const isSubStart = /^([a-d]\.|[1-3]°|①|②|③|\([1-4]\))/.test(rawTxt);
        const isContLine = /^(=|⇒|⟹|∴|⇔|⟺|≈|≤|≥)/.test(rawTxt);

        if (lineIdx === 0) {
          // 首行有时贴左、有时直接从题干中段偏右起笔（如 w6 Q2/Q3）
          leftDrift = (rand() < 0.35) ? (26 + rand() * 42 * TJ) : (4 + rand() * 18 * TJ);
        } else if (isSubStart) {
          // 新小问（a./b./1°/2°）：回退到偏左但绝不对齐在同一条垂直线上
          leftDrift = Math.max(2, 4 + rand() * 26 * TJ + randSym(10 * TJ));
        } else if (isContLine || rand() < 0.52) {
          // 推导续行/结论行：明显向右阶梯缩进（+14 ~ +46px），复刻 w7 Q1/Q10 阶梯手写流
          leftDrift = Math.min(92 * TJ, leftDrift + 12 + rand() * 32 * TJ);
        } else {
          // 普通换行：较大幅度自由漂移（-22 ~ +32px）
          leftDrift = Math.max(2, Math.min(82 * TJ, leftDrift + randSym(28 * TJ) + 4));
        }
        lineIdx++;

        const existingMargin = parseFloat(getComputedStyle(line).marginLeft) || 0;
        line.style.marginLeft = `${(existingMargin + leftDrift).toFixed(1)}px`;

        // 行距忽紧忽松：有时两行上下紧贴（-2.8px），有时换步留出呼吸空隙（+11px）
        const tightLine = rand() < 0.36;
        const mt = tightLine ? (-2.6 + rand() * 2.4) : ((rand() * 6.5 - 0.5) * Math.min(1.25, TJ));
        const mb = tightLine ? (0.8 + rand() * 2.6) : (3.0 + rand() * 8.5 * Math.min(1.25, TJ));
        line.style.marginTop = `${mt.toFixed(1)}px`;
        line.style.marginBottom = `${mb.toFixed(1)}px`;

        if (!line.matches('ul, ol')) {
          const tilt = blockBiasDeg + randSym(1.25 * Math.max(MJ, 0.55)) - 0.08;
          const lineScale = 0.945 + rand() * 0.095;
          line.style.transform = `rotate(${tilt.toFixed(2)}deg) scale(${lineScale.toFixed(3)})`;
          line.style.transformOrigin = 'left center';
        }
      });

      // 同行并排公式（\qquad / .hw-eq-gap）：右侧公式独立上下错位（±3~8px）并微转角度，打破同行水平死板对齐！
      block.querySelectorAll('.hw-eq-gap').forEach(g => {
        g.style.width = `${(1.35 + rand() * 2.1).toFixed(2)}em`;
        const sib = g.nextElementSibling;
        if (sib) {
          const sDy = (rand() < 0.5 ? -1 : 1) * (2.6 + rand() * 5.2) * Math.max(0.6, MJ);
          const sRot = randSym(1.45 * Math.max(0.6, MJ));
          const sScale = 0.94 + rand() * 0.10;
          sib.style.display = 'inline-block';
          const prevT = sib.style.transform || '';
          sib.style.transform = `${prevT} translateY(${sDy.toFixed(1)}px) rotate(${sRot.toFixed(2)}deg) scale(${sScale.toFixed(3)})`.trim();
        }
      });

      // 公式内部手腕弧线起伏 + 等号/箭头换手台阶落差（复刻 w6 Q1 第二等号下沉 ~6px 的真实手写轨迹！）
      block.querySelectorAll('.katex-html').forEach(khtml => {
        let eqDrift = randSym(1.2 * MJ);
        const eqSlope = randSym(0.42 * MJ);
        const eqPhase = rand() * Math.PI * 2;
        const eqFreq = 0.18 + rand() * 0.16;
        const archAmp = randSym(3.4 * MJ);
        const bases = Array.from(khtml.querySelectorAll(':scope > .base'));
        let totalChildren = 0;
        bases.forEach(b => { totalChildren += b.children.length; });
        let idx = 0;
        bases.forEach(base => {
          Array.from(base.children).forEach(child => {
            if (child.classList.contains('strut') || child.classList.contains('mspace')) return;
            idx++;
            const prog = idx / Math.max(1, totalChildren);
            // 遇到等号/箭头推演符号时，模拟人手挪腕重新起笔的垂直台阶落差（±2~4.5px）
            if (child.classList.contains('mrel')) {
              eqDrift += randSym(2.4 * MJ);
            } else {
              eqDrift = eqDrift * 0.86 + randSym(0.68 * MJ);
            }
            const arch = Math.sin(prog * Math.PI) * archAmp;
            const wave = Math.sin(idx * eqFreq + eqPhase) * 1.65 * MJ;
            const dy = Math.max(-8.5, Math.min(8.5, eqDrift + idx * eqSlope + arch + wave + randSym(0.55 * MJ)));
            const rot = randSym(1.25 * MJ);
            child.style.display = child.style.display || 'inline-block';
            const prevT = child.style.transform || '';
            child.style.transform = `${prevT} translateY(${dy.toFixed(2)}px) rotate(${rot.toFixed(2)}deg)`.trim();
          });
        });
      });

      // Opt #2: 短小问/短结论自然同行串写（复刻 w7 Q1/Q10/Q15 与 w6 Q3 两栏错落手写习惯！）
      if (block.classList.contains('hw-answer')) {
        const availW = block.clientWidth - 26;
        const kids = Array.from(block.children).filter(el => el.matches('p, .hw-math-display'));
        for (let ci = 0; ci < kids.length - 1; ci++) {
          const a = kids[ci];
          const b = kids[ci + 1];
          if (a.dataset.chained || b.dataset.chained) continue;
          if (a.nextElementSibling !== b) continue;
          const rA = a.getBoundingClientRect();
          const rB = b.getBoundingClientRect();
          if (rA.height > 54 || rB.height > 54) continue;
          // 测量实际内容宽（不看 block 满行宽）
          let maxA = rA.left, maxB = rB.left;
          a.querySelectorAll('svg, span.hw-ch, .katex').forEach(n => { const rc = n.getBoundingClientRect(); if (rc.right > maxA) maxA = rc.right; });
          b.querySelectorAll('svg, span.hw-ch, .katex').forEach(n => { const rc = n.getBoundingClientRect(); if (rc.right > maxB) maxB = rc.right; });
          const wA = maxA - rA.left;
          const wB = maxB - rB.left;
          const gapPx = 22 + rand() * 20;
          if (wA > 18 && wB > 18 && wA < availW * 0.46 && wB < availW * 0.44 && (wA + gapPx + wB) < availW * 0.86) {
            if (rand() < 0.46) {
              const rowWrap = document.createElement('div');
              rowWrap.style.display = 'flex';
              rowWrap.style.alignItems = 'baseline';
              rowWrap.style.flexWrap = 'nowrap';
              rowWrap.style.marginLeft = a.style.marginLeft || '4px';
              rowWrap.style.marginTop = a.style.marginTop || '1px';
              rowWrap.style.marginBottom = b.style.marginBottom || '3px';
              a.parentNode.insertBefore(rowWrap, a);
              a.style.marginLeft = '0px';
              a.style.marginTop = '0px';
              a.style.marginBottom = '0px';
              b.style.marginLeft = `${gapPx.toFixed(1)}px`;
              b.style.marginTop = '0px';
              b.style.marginBottom = '0px';
              const sDy = (rand() < 0.5 ? -1 : 1) * (2.2 + rand() * 4.2);
              const prevTB = b.style.transform || '';
              b.style.transform = `translateY(${sDy.toFixed(1)}px) ${prevTB}`.trim();
              rowWrap.appendChild(a);
              rowWrap.appendChild(b);
              a.dataset.chained = '1';
              b.dataset.chained = '1';
              ci++;
            }
          }
        }
      }
    });

    // Opt #4: 极低频真人划错重起微瑕（每 ~2 页至多 1 处，支持 --no-quirks 关闭）
    if (QUIRKS_ENABLED && PG) {
      const candBlocks = Array.from(document.querySelectorAll('.hw-answer > p, .hw-answer > .hw-math-display'));
      const maxQuirks = Math.max(0, Math.min(2, Math.floor(candBlocks.length / 18)));
      const falseChars = ['x', '2', 'n', 'E', 'P'];
      let placed = 0;
      for (let qi = 5; qi < candBlocks.length - 3 && placed < maxQuirks; qi += 17) {
        if (rand() > 0.65) continue;
        const targetLine = candBlocks[qi];
        const firstK = targetLine.querySelector('.katex-html > .base');
        if (!firstK) continue;
        const fc = falseChars[Math.floor(rand() * falseChars.length)];
        const fInfo = pickVariant(fc);
        if (!fInfo) continue;

        const wrap = document.createElement('span');
        wrap.style.position = 'relative';
        wrap.style.display = 'inline-block';
        wrap.style.marginRight = '3.5px';
        wrap.style.opacity = '0.84';
        const fSvg = makePersonalSvg(fc, fInfo, true, BASE_FONT_PX * 0.92);
        wrap.appendChild(fSvg);

        const slash = document.createElementNS(SVG_NS, 'svg');
        slash.setAttribute('class', 'hw-hand-svg');
        slash.setAttribute('viewBox', '0 0 14 16');
        slash.style.width = '14px';
        slash.style.height = '16px';
        slash.style.left = '-2px';
        slash.style.top = '-2px';
        const sp1 = document.createElementNS(SVG_NS, 'path');
        sp1.setAttribute('d', `M 1.5 ${(13.5 + randSym(1.2)).toFixed(1)} Q 7 7.5 12.2 ${(2.0 + randSym(1.2)).toFixed(1)} M 3.8 ${(14.2 + randSym(1.0)).toFixed(1)} Q 8.5 8.5 13.4 ${(3.2 + randSym(1.0)).toFixed(1)}`);
        sp1.style.strokeWidth = '0.72px';
        slash.appendChild(sp1);
        wrap.appendChild(slash);

        firstK.insertBefore(wrap, firstK.firstChild);
        placed++;
      }
    }
  }

  // 5b. Hand-drawn vector bullets (zero CSS text pseudo-elements) & hand-drawn Markdown table rules
  document.querySelectorAll('.hw-answer ul > li').forEach(li => {
    if (li.querySelector(':scope > svg.hw-bullet-svg')) return;
    const bSvg = document.createElementNS(SVG_NS, 'svg');
    bSvg.setAttribute('class', 'pg-svg hw-bullet-svg');
    bSvg.dataset.cat = 'struct';
    bSvg.dataset.ch = 'bullet';
    bSvg.setAttribute('viewBox', '0 0 8 8');
    bSvg.style.width = '5.2px';
    bSvg.style.height = '5.2px';
    const bp = document.createElementNS(SVG_NS, 'path');
    bp.setAttribute('d', 'M2.2 2.1 Q4.1 1.4 5.9 2.6 Q6.4 4.4 5.3 5.9 Q3.4 6.4 2.0 5.1 Q1.6 3.5 2.2 2.1 Z');
    bSvg.appendChild(bp);
    li.insertBefore(bSvg, li.firstChild);
  });

  document.querySelectorAll('.hw-answer table').forEach(tbl => {
    if (tbl.closest('.hw-table-wrap')) return;
    const wrap = document.createElement('div');
    wrap.className = 'hw-table-wrap';
    tbl.parentNode.insertBefore(wrap, tbl);
    wrap.appendChild(tbl);

    const wRect = wrap.getBoundingClientRect();
    const tw = Math.max(20, wRect.width);
    const th = Math.max(16, wRect.height);
    const rows = Array.from(tbl.querySelectorAll('tr'));
    if (!rows.length) return;

    const colRightXs = [];
    const firstCells = Array.from(rows[0].children);
    for (let ci = 0; ci < firstCells.length - 1; ci++) {
      const cr = firstCells[ci].getBoundingClientRect();
      colRightXs.push(cr.right - wRect.left);
    }
    const rowTopYs = [0.8];
    rows.forEach((tr, ri) => {
      const rr = tr.getBoundingClientRect();
      rowTopYs.push(rr.bottom - wRect.top - (ri === rows.length - 1 ? 0.8 : 0.0));
    });

    const tSvg = document.createElementNS(SVG_NS, 'svg');
    tSvg.setAttribute('class', 'hw-hand-svg hw-fill-svg');
    tSvg.dataset.cat = 'struct';
    tSvg.dataset.ch = 'table_rule';
    tSvg.setAttribute('viewBox', `0 0 ${tw.toFixed(1)} ${th.toFixed(1)}`);
    tSvg.style.left = '0px';
    tSvg.style.top = '0px';
    tSvg.style.width = `${tw.toFixed(1)}px`;
    tSvg.style.height = `${th.toFixed(1)}px`;
    tSvg.style.color = getComputedStyle(tbl).color;

    const addRule = (x0, y0, x1, y1, hw) => {
      const dx = x1 - x0, dy = y1 - y0;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const arch = randSym(1.0);
      const pts = sampleQuadBezier(x0, y0, (x0 + x1) * 0.5 + nx * arch, (y0 + y1) * 0.5 + ny * arch, x1, y1, 3.6);
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', strokePtsToStarNoteRibbon(pts, hw, rand() * 6.28));
      tSvg.appendChild(p);
    };

    rowTopYs.forEach((ry, ri) => {
      const overL = 1.5 + rand() * 2.5;
      const overR = 1.5 + rand() * 2.5;
      const hw = (ri === 1 && tbl.querySelector('thead')) ? 0.44 : 0.36;
      addRule(-overL, ry + randSym(0.6), tw + overR, ry + randSym(0.6), hw);
    });
    // Vertical dividers (left border, inner cols, right border) with natural hand overshoots
    [0.8, ...colRightXs, tw - 0.8].forEach(cx => {
      const overT = 1.0 + rand() * 2.2;
      const overB = 1.0 + rand() * 2.2;
      addRule(cx + randSym(0.6), -overT, cx + randSym(0.6), th + overB, 0.35);
    });
    wrap.appendChild(tSvg);
  });

  // 6. 复刻真实 iPad 在固定留空原题卷上作答的“有时留出大片空白、有时快写不下挤一挤”自然版面（--layout natural）
  if (LAYOUT_MODE === 'natural') {
    document.querySelectorAll('.ws-question[data-slot-mm]').forEach(sec => {
      const ans = sec.querySelector('.hw-answer');
      if (!ans || !ans.textContent.trim() && !ans.querySelector('svg')) return;
      const slotMm = parseFloat(sec.getAttribute('data-slot-mm') || '44');
      const slotPx = slotMm * 3.7795;
      const actualH = ans.scrollHeight;
      const lines = Array.from(ans.querySelectorAll(':scope > p, :scope > .hw-math-display, li > .hw-math-display'));

      if (actualH < slotPx * 0.62) {
        // 短解答：自然留出较多空白（如 w9 Q2/Q4、w5 Q3），且偶尔起笔偏中左/靠下，不千篇一律贴左上角
        const keepRatio = 0.78 + rand() * 0.24;
        ans.style.minHeight = `${(slotPx * keepRatio).toFixed(1)}px`;
        const extraIndent = (rand() < 0.45) ? (22 + rand() * 34) : (6 + rand() * 14);
        ans.style.paddingLeft = `${extraIndent.toFixed(1)}px`;
        ans.style.marginTop = `${(4 + rand() * 11).toFixed(1)}px`;
        sec.style.marginBottom = `${(10 + rand() * 10).toFixed(1)}px`;
      } else if (actualH <= slotPx * 0.96) {
        // 中等解答：留白适中、参差自然
        const keepRatio = 0.90 + rand() * 0.14;
        ans.style.minHeight = `${(slotPx * keepRatio).toFixed(1)}px`;
        ans.style.paddingLeft = `${(5 + rand() * 14).toFixed(1)}px`;
        ans.style.marginTop = `${(2 + rand() * 6).toFixed(1)}px`;
        sec.style.marginBottom = `${(8 + rand() * 8).toFixed(1)}px`;
      } else {
        // 长解答（快写不下/溢槽）：模拟写到后半段空间告急，起笔更靠左靠上、后半段渐进收紧字距行距并贴近下一题题干！
        ans.style.paddingLeft = `${(1 + rand() * 5).toFixed(1)}px`;
        ans.style.marginTop = `${(0 + rand() * 2).toFixed(1)}px`;
        const cramRatio = Math.min(1.45, actualH / Math.max(80, slotPx));
        lines.forEach((ln, idx) => {
          if (idx >= 1) {
            const progress = idx / Math.max(1, lines.length - 1);
            const shrink = Math.max(0.87, 1.0 - (cramRatio - 0.90) * 0.14 * progress);
            ln.style.marginTop = `${(-1.2 - progress * 2.2).toFixed(1)}px`;
            ln.style.marginBottom = `${(0.5 + (1 - progress) * 1.6).toFixed(1)}px`;
            if (shrink < 0.985) {
              const curT = ln.style.transform || '';
              ln.style.transformOrigin = 'left center';
              ln.style.transform = `${curT} scale(${shrink.toFixed(3)})`.trim();
            }
          }
        });
        // 写满贴边：底部与下一题仅剩 1~4px 极小空隙（像 w8 Q2/Q4 那样写到下一题头顶）
        sec.style.marginBottom = `${(1 + rand() * 3.5).toFixed(1)}px`;
      }
    });
  }

  // 6b. PDF overlay slot auto-fit: keep handwritten block strictly inside its blank box height
  document.querySelectorAll('.ov-box[data-slot-mm]').forEach(boxEl => {
    const slotMm = parseFloat(boxEl.getAttribute('data-slot-mm') || '0');
    if (slotMm <= 0) return;
    const probe = document.createElement('div');
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;width:10mm;height:10mm;pointer-events:none;';
    document.body.appendChild(probe);
    const pxMm = Math.max(2.0, probe.getBoundingClientRect().width / 10.0);
    probe.remove();
    const maxPx = slotMm * pxMm * 0.94;
    if (boxEl.scrollHeight > maxPx && boxEl.scrollHeight > 10) {
      boxEl.querySelectorAll('p, .hw-math-display').forEach(el => {
        el.style.marginTop = '-1.2px';
        el.style.marginBottom = '1.2px';
      });
      if (boxEl.scrollHeight > maxPx) {
        const sc = Math.max(0.74, maxPx / boxEl.scrollHeight);
        boxEl.style.transformOrigin = 'left top';
        boxEl.style.transform = `scale(${sc.toFixed(3)})`;
      }
    }
  });

  // 7. Phase 2: 采集 InkScene 毫秒级物理矢量几何（svg 坐标/线宽/路径/换行归属），支持无损局部微调与秒级重绘
  if (CFG.captureScene) {
    try {
      const probe = document.createElement('div');
      probe.style.cssText = 'position:absolute;left:-9999px;top:0;width:100mm;height:10mm;pointer-events:none;';
      document.body.appendChild(probe);
      const pxPerMm = Math.max(2.0, probe.getBoundingClientRect().width / 100.0);
      probe.remove();

      const margins = Array.isArray(CFG.marginsMm) ? CFG.marginsMm : [13, 14, 13, 14];
      const mTop = margins[0], mBottom = margins[2], mLeft = margins[3];
      const pageH = CFG.pageHeightMm || 297.0;
      const usableH = Math.max(60.0, pageH - mTop - mBottom);
      const rootRect = document.getElementById('page-root')?.getBoundingClientRect() || { left: 0, top: 0 };

      function rgbToHex(col) {
        const m = String(col || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
        if (!m) return '#111318';
        return '#' + [1, 2, 3].map(i => Math.max(0, Math.min(255, parseInt(m[i], 10))).toString(16).padStart(2, '0')).join('');
      }

      function parseDeg(trStr) {
        const m = String(trStr || '').match(/rotate\(([-\d.]+)deg\)/);
        return m ? parseFloat(m[1]) : 0.0;
      }

      document.querySelectorAll('main#page-root .hw-table-wrap').forEach((el, idx) => {
        el.dataset.hwGroup = `tbl_${idx}`;
      });
      document.querySelectorAll('main#page-root .hw-sketch-wrap').forEach((el, idx) => {
        el.dataset.hwGroup = `sk_${idx}`;
      });

      // Model break-inside: avoid page pushes so scene.json pageIdx & yMm match Chrome print pagination
      const avoidSelectors = 'main#page-root .hw-answer p, main#page-root .hw-answer li, main#page-root .hw-chain-cont, main#page-root .hw-math-display, main#page-root .hw-table-wrap, main#page-root .hw-sketch-wrap';
      const breakShifts = [];
      let cumShiftMm = 0;
      document.querySelectorAll(avoidSelectors).forEach(blk => {
        if (blk.closest('.ov-page')) return;
        const brc = blk.getBoundingClientRect();
        if (brc.height < 1) return;
        const bTop = (brc.top - rootRect.top) / pxPerMm + cumShiftMm;
        const bH = brc.height / pxPerMm;
        if (bH >= usableH * 0.96) return;
        const pStart = Math.max(0, Math.floor(bTop / usableH));
        const pEnd = Math.max(0, Math.floor((bTop + bH - 0.2) / usableH));
        if (pEnd > pStart) {
          const pushMm = (pStart + 1) * usableH - bTop;
          cumShiftMm += pushMm;
          breakShifts.push({ rawTopMm: (brc.top - rootRect.top) / pxPerMm, shiftMm: cumShiftMm });
        }
      });
      function effectiveYRelMm(rawYRelMm) {
        let add = 0;
        for (let i = 0; i < breakShifts.length; i++) {
          if (rawYRelMm + 0.05 >= breakShifts[i].rawTopMm) add = breakShifts[i].shiftMm;
          else break;
        }
        return rawYRelMm + add;
      }

      const rawGlyphs = [];
      let maxPage = Math.max(0, document.querySelectorAll('.ov-page').length - 1);
      document.querySelectorAll('main#page-root svg.pg-svg, main#page-root svg.hw-hand-svg').forEach(sv => {
        const rc = sv.getBoundingClientRect();
        if (rc.width < 0.25 || rc.height < 0.25) return;
        const cs = getComputedStyle(sv);
        if (cs.opacity === '0' || cs.display === 'none') return;

        const vbParts = (sv.getAttribute('viewBox') || '0 0 100 100').trim().split(/\s+/).map(Number);
        const vb = (vbParts.length === 4 && vbParts.every(n => Number.isFinite(n))) ? vbParts : [0, 0, 100, 100];
        const paths = [];
        sv.querySelectorAll('path').forEach(p => {
          const d = p.getAttribute('d');
          if (!d) return;
          const pcs = getComputedStyle(p);
          const isFill = pcs.fill !== 'none' && p.getAttribute('fill') !== 'none';
          const sw = parseFloat(pcs.strokeWidth) || (isFill ? 0.04 : 0.74);
          const dCol = p.getAttribute('data-color');
          const pObj = { d, fill: isFill, sw: Number(sw.toFixed(3)) };
          if (dCol) pObj.color = dCol;
          paths.push(pObj);
        });
        if (!paths.length) return;

        const ovPage = sv.closest('.ov-page[data-page]');
        let pageIdx, xMm, yMm;
        if (ovPage) {
          const ovRect = ovPage.getBoundingClientRect();
          pageIdx = Math.max(0, parseInt(ovPage.dataset.page || '0', 10) || 0);
          xMm = (rc.left - ovRect.left) / pxPerMm;
          yMm = (rc.top - ovRect.top) / pxPerMm;
        } else {
          const xRelMm = (rc.left - rootRect.left) / pxPerMm;
          const yRelMm = effectiveYRelMm((rc.top - rootRect.top) / pxPerMm);
          pageIdx = Math.max(0, Math.floor(yRelMm / usableH));
          xMm = mLeft + xRelMm;
          yMm = mTop + (yRelMm - pageIdx * usableH);
        }
        const wMm = rc.width / pxPerMm;
        const hMm = rc.height / pxPerMm;
        if (pageIdx > maxPage) maxPage = pageIdx;
        const qSec = sv.closest('[data-qnum]');
        const grpEl = sv.closest('[data-hw-group]');
        rawGlyphs.push({
          page: pageIdx,
          ch: sv.dataset.ch || '',
          cat: sv.dataset.cat || (sv.classList.contains('pg-svg') ? 'glyph' : 'struct'),
          vidx: parseInt(sv.dataset.vidx || '0', 10) || 0,
          in_math: sv.dataset.inMath === '1',
          qnum: qSec ? String(qSec.dataset.qnum || '') : '',
          group: grpEl ? (grpEl.dataset.hwGroup || '') : '',
          x: Number(xMm.toFixed(3)),
          y: Number(yMm.toFixed(3)),
          w: Number(wMm.toFixed(3)),
          h: Number(hMm.toFixed(3)),
          rot: Number(parseDeg(sv.style.transform).toFixed(2)),
          color: rgbToHex(cs.color),
          vb,
          paths,
        });
      });

      const obstacles = Array.isArray(CFG.presetObstacles) ? CFG.presetObstacles.slice() : [];
      document.querySelectorAll('main#page-root .ws-prompt, main#page-root .ws-header, main#page-root .st-header').forEach(el => {
        // Standalone .st-header contains handwritten glyphs themselves — do not treat them as external prompt obstacles
        if (el.classList.contains('st-header') && el.querySelector('svg.pg-svg')) return;
        const rc = el.getBoundingClientRect();
        if (rc.width < 1 || rc.height < 1) return;
        const xRel = (rc.left - rootRect.left) / pxPerMm;
        const yRel = effectiveYRelMm((rc.top - rootRect.top) / pxPerMm);
        const pageIdx = Math.max(0, Math.floor(yRel / usableH));
        obstacles.push({
          page: pageIdx,
          x: Number((mLeft + xRel).toFixed(2)),
          y: Number((mTop + (yRel - pageIdx * usableH)).toFixed(2)),
          w: Number((rc.width / pxPerMm).toFixed(2)),
          h: Number((rc.height / pxPerMm).toFixed(2)),
        });
      });

      // Group glyphs per page into visual lines (median center y within 3.4 mm)
      const finalGlyphs = [];
      const lines = [];
      for (let p = 0; p <= maxPage; p++) {
        const pGlyphs = rawGlyphs.filter(g => g.page === p).sort((a, b) => (a.y + a.h * 0.5) - (b.y + b.h * 0.5));
        const buckets = [];
        pGlyphs.forEach(g => {
          const cy = g.y + g.h * 0.5;
          let targetB = null;
          for (let bi = buckets.length - 1; bi >= Math.max(0, buckets.length - 3); bi--) {
            if (Math.abs(cy - buckets[bi].cy) <= 3.4) {
              targetB = buckets[bi];
              break;
            }
          }
          if (!targetB) {
            targetB = { cy, items: [] };
            buckets.push(targetB);
          }
          targetB.items.push(g);
          targetB.cy = targetB.items.reduce((acc, it) => acc + it.y + it.h * 0.5, 0) / targetB.items.length;
        });

        let gCounter = 0;
        buckets.sort((a, b) => a.cy - b.cy).forEach((b, li) => {
          const lineId = `p${p}l${li}`;
          b.items.sort((a, c) => a.x - c.x);
          const gIds = [];
          let txt = '';
          b.items.forEach(g => {
            g.id = `p${p}g${gCounter++}`;
            g.line = lineId;
            gIds.push(g.id);
            txt += g.ch || '?';
            finalGlyphs.push(g);
          });
          const xs = b.items.map(it => it.x);
          const rs = b.items.map(it => it.x + it.w);
          const ys = b.items.map(it => it.y + it.h * 0.5);
          const hs = b.items.map(it => it.h);
          lines.push({
            id: lineId,
            page: p,
            glyph_ids: gIds,
            text: txt,
            left_mm: Number(Math.min(...xs).toFixed(2)),
            right_mm: Number(Math.max(...rs).toFixed(2)),
            baseline_y_mm: Number((ys.reduce((a, c) => a + c, 0) / ys.length).toFixed(2)),
            med_h_mm: Number((hs.sort((a, c) => a - c)[Math.floor(hs.length / 2)]).toFixed(2)),
            grid: !!CFG.gridLock,
          });
        });
      }

      const resp = await fetch('/__scene_upload__', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pages_detected: maxPage + 1,
          glyphs: finalGlyphs,
          lines,
          obstacles,
        }),
      });
      if (!resp.ok) {
        window.__INKTWIN_SCENE_ERROR__ = `HTTP ${resp.status}`;
        console.error('[InkTwin scene upload failed]', resp.status);
      }
    } catch (e) {
      window.__INKTWIN_SCENE_ERROR__ = String((e && e.message) || e);
      console.error('[InkTwin scene capture error]', e);
    }
  }

  window.__HW_READY__ = true;
})();
