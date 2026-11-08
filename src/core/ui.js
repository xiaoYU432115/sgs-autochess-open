// src/core/ui.js
// 轻量 DOM 工具，不依赖任何 UI 框架。供各插件与插件管理器复用。

export function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const k in props) {
    const v = props[k];
    if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else if (k === 'class' || k === 'className') e.className = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c == null) return;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return e;
}

// 固定右下角的可折叠面板；返回 { root, body, add }
export function panel(title) {
  const root = el('div', {
    style: {
      position: 'fixed', right: '8px', top: '8px', zIndex: 99999,
      background: 'rgba(0,0,0,.82)', color: '#fff', font: '12px/1.4 sans-serif',
      padding: '8px', borderRadius: '6px', maxWidth: '340px', maxHeight: '80vh', overflow: 'auto',
    },
  });
  if (title) root.appendChild(el('div', { text: title, style: { fontWeight: 'bold', marginBottom: '4px', cursor: 'move' } }));
  const body = el('div');
  root.appendChild(body);
  if (document.body) document.body.appendChild(root);
  return {
    root, body,
    add(node) { body.appendChild(node); return node; },
  };
}

export function button(label, onClick) {
  return el('button', {
    text: label, onClick,
    style: { margin: '2px', padding: '2px 6px', cursor: 'pointer', background: '#2b6cb0', color: '#fff', border: 'none', borderRadius: '3px' },
  });
}

export function toggle(label, getVal, setVal) {
  const cb = el('input', { type: 'checkbox', checked: !!getVal() });
  cb.addEventListener('change', () => setVal(cb.checked));
  return el('label', { style: { display: 'inline-flex', alignItems: 'center', gap: '4px', margin: '2px' } }, [cb, label]);
}
