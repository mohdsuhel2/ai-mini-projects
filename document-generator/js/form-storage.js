(function (global) {
  const PREFIX = 'noobius_form_v1_';
  const DEBOUNCE_MS = 300;
  const LARGE_FIELD_KEYS = new Set([
    'landlordSignatureDataUrl',
    'revenueStampDataUrl',
    'signatureDataUrl',
    'lineItems',
  ]);
  const flushers = [];
  let schemaRegistry = null;
  let extraForKeyFn = null;

  function debounce(fn, wait) {
    let timer;
    const debounced = function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
    debounced.flush = function (...args) {
      clearTimeout(timer);
      fn.apply(this, args);
    };
    return debounced;
  }

  function stripLargeFields(data) {
    if (!data || typeof data !== 'object') return data;
    const next = { ...data };
    LARGE_FIELD_KEYS.forEach(key => {
      delete next[key];
    });
    return next;
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
    const payload = stripLargeFields(data);
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(payload));
      return true;
    } catch (err) {
      try {
        localStorage.setItem(PREFIX + key, JSON.stringify(stripLargeFields(payload)));
      } catch (_) {}
      return false;
    }
  }

  function hasSaved(key) {
    return !!read(key);
  }

  function isStorable(el) {
    if (!el || !el.id) return false;
    if (el.disabled) return false;
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
      } else if (!el.readOnly) {
        out[id] = el.value;
      }
    });
    return out;
  }

  function applyByIds(data, options = {}) {
    const silent = options.silent !== false;
    if (!data || typeof data !== 'object') return false;
    let applied = false;
    Object.entries(data).forEach(([id, value]) => {
      if (LARGE_FIELD_KEYS.has(id)) return;
      const el = document.getElementById(id);
      if (!el || value == null) return;
      if (el.type === 'file' || el.type === 'button' || el.type === 'submit') return;
      if (el.disabled) return;
      if (el.type === 'checkbox') {
        el.checked = !!value;
      } else if (el.type === 'radio') {
        el.checked = el.value === value;
      } else if (el.readOnly) {
        return;
      } else {
        el.value = value;
      }
      if (!silent) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      applied = true;
    });
    return applied;
  }

  function saveSchemaNow(key) {
    if (!schemaRegistry || !schemaRegistry[key]) return;
    const payload = collectByIds(schemaRegistry[key]);
    if (typeof extraForKeyFn === 'function') {
      Object.assign(payload, extraForKeyFn(key) || {});
    }
    write(key, payload);
  }

  function bindSchemas(schemas, extraForKey, rootSelector = '#configPanelScroll') {
    schemaRegistry = schemas;
    extraForKeyFn = extraForKey;
    const root = document.querySelector(rootSelector);
    if (!root) return;

    const idToKey = new Map();
    Object.entries(schemas).forEach(([key, ids]) => {
      ids.forEach(id => idToKey.set(id, key));
    });

    const debouncedSavers = new Map();
    Object.keys(schemas).forEach(key => {
      const debounced = debounce(() => saveSchemaNow(key), DEBOUNCE_MS);
      debouncedSavers.set(key, debounced);
      flushers.push(debounced.flush);
      flushers.push(() => saveSchemaNow(key));
    });

    const resolveKey = target => {
      if (!target || !target.id) return null;
      return idToKey.get(target.id) || null;
    };

    root.addEventListener('input', event => {
      const key = resolveKey(event.target);
      if (key) debouncedSavers.get(key)?.();
    }, true);

    root.addEventListener('change', event => {
      const key = resolveKey(event.target);
      if (key) debouncedSavers.get(key)?.();
    }, true);

    root.addEventListener('focusout', event => {
      const key = resolveKey(event.target);
      if (key) saveSchemaNow(key);
    }, true);
  }

  function bindAutoSave(key, ids, onSave) {
    const saveNow = () => {
      const payload = collectByIds(ids);
      if (onSave) Object.assign(payload, onSave() || {});
      write(key, payload);
    };
    const save = debounce(saveNow, DEBOUNCE_MS);
    flushers.push(save.flush);
    flushers.push(saveNow);
    return save;
  }

  function restore(key, ids, onApply) {
    const saved = read(key);
    if (!saved) return false;
    const fields = { ...saved };
    if (onApply) Object.assign(fields, onApply(fields) || {});
    return applyByIds(fields, { silent: true });
  }

  function flushAll() {
    if (schemaRegistry) {
      Object.keys(schemaRegistry).forEach(saveSchemaNow);
      return;
    }
    flushers.forEach(flush => {
      try { flush(); } catch (_) {}
    });
  }

  function persistAll(schemas, extraForKey) {
    if (!schemas || typeof schemas !== 'object') return;
    Object.entries(schemas).forEach(([key, ids]) => {
      const payload = collectByIds(ids);
      if (typeof extraForKey === 'function') {
        Object.assign(payload, extraForKey(key) || {});
      }
      write(key, payload);
    });
  }

  window.addEventListener('pagehide', flushAll);
  window.addEventListener('beforeunload', flushAll);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushAll();
  });

  global.NOOBIUS_FORM_STORAGE = {
    PREFIX,
    read,
    write,
    hasSaved,
    collectByIds,
    applyByIds,
    bindSchemas,
    bindAutoSave,
    restore,
    flushAll,
    persistAll,
    saveSchemaNow,
  };
})(window);
