(function (global) {
  const PREFIX = 'noobius_form_v1_';
  const DEBOUNCE_MS = 450;

  function debounce(fn, wait) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function read(key) {
    try {
      const raw = localStorage.getItem(PREFIX + key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function write(key, data) {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(data));
    } catch (_) {}
  }

  function hasSaved(key) {
    return !!read(key);
  }

  function isStorable(el) {
    if (!el || !el.id) return false;
    if (el.disabled || el.readOnly) return false;
    if (el.type === 'file' || el.type === 'button' || el.type === 'submit') return false;
    return true;
  }

  function collectByIds(ids) {
    const out = {};
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (!isStorable(el)) return;
      if (el.type === 'checkbox') out[id] = el.checked;
      else if (el.type === 'radio') {
        if (el.checked) out[id] = el.value;
      } else out[id] = el.value;
    });
    return out;
  }

  function applyByIds(data) {
    if (!data || typeof data !== 'object') return false;
    let applied = false;
    Object.entries(data).forEach(([id, value]) => {
      const el = document.getElementById(id);
      if (!el || value == null) return;
      if (el.type === 'file' || el.type === 'button' || el.type === 'submit') return;
      if (el.disabled) return;
      if (el.type === 'checkbox') {
        el.checked = !!value;
      } else if (el.type === 'radio') {
        el.checked = el.value === value;
      } else if (el.readOnly && el.value === value) {
        return;
      } else if (el.readOnly) {
        return;
      } else {
        el.value = value;
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      applied = true;
    });
    return applied;
  }

  function bindAutoSave(key, ids, onSave) {
    const save = debounce(() => {
      const payload = collectByIds(ids);
      if (onSave) Object.assign(payload, onSave() || {});
      write(key, payload);
    }, DEBOUNCE_MS);

    ids.forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', save);
      el.addEventListener('change', save);
    });

    return save;
  }

  function restore(key, ids, onApply) {
    const saved = read(key);
    if (!saved) return false;
    const fields = { ...saved };
    if (onApply) Object.assign(fields, onApply(fields) || {});
    return applyByIds(fields);
  }

  global.NOOBIUS_FORM_STORAGE = {
    PREFIX,
    read,
    write,
    hasSaved,
    collectByIds,
    applyByIds,
    bindAutoSave,
    restore,
  };
})(window);
