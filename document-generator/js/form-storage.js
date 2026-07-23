(function (global) {
  const PREFIX = 'noobius_form_v1_';
  const DEBOUNCE_MS = 300;
  const LARGE_FIELD_KEYS = new Set([
    'landlordSignatureDataUrl',
    'revenueStampDataUrl',
    'signatureDataUrl',
    'lineItems',
  ]);
  const USER_PROFILE_KEY = 'user_profile';
  const flushers = [];
  let schemaRegistry = null;
  let extraForKeyFn = null;
  let profileGroups = null;
  let idToProfileGroup = null;
  let profileSyncing = false;

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

  function getFieldValue(el) {
    if (!el) return undefined;
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'radio') return el.checked ? el.value : undefined;
    return el.value;
  }

  function setFieldValue(el, value, silent = true) {
    if (!el || value == null) return;
    if (el.type === 'checkbox') {
      el.checked = !!value;
    } else if (el.type === 'radio') {
      el.checked = el.value === value;
    } else if (!el.readOnly) {
      el.value = value;
    }
    if (!silent) {
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function readProfileGroupValue(groupKey) {
    if (!profileGroups || !profileGroups[groupKey]) return undefined;
    for (const id of profileGroups[groupKey]) {
      const el = document.getElementById(id);
      if (!el || !isStorable(el)) continue;
      const value = getFieldValue(el);
      if (value !== undefined && String(value).trim() !== '') return value;
    }
    for (const id of profileGroups[groupKey]) {
      const el = document.getElementById(id);
      if (!el || !isStorable(el)) continue;
      const value = getFieldValue(el);
      if (value !== undefined) return value;
    }
    return undefined;
  }

  function saveProfileGroupNow(groupKey) {
    if (!profileGroups || !profileGroups[groupKey]) return;
    const profile = read(USER_PROFILE_KEY) || {};
    profile[groupKey] = readProfileGroupValue(groupKey);
    write(USER_PROFILE_KEY, profile);
  }

  function syncProfileGroupSiblings(sourceId, groupKey, value) {
    if (profileSyncing || !profileGroups || !profileGroups[groupKey]) return;
    profileSyncing = true;
    profileGroups[groupKey].forEach(id => {
      if (id === sourceId) return;
      const el = document.getElementById(id);
      if (!el || !isStorable(el)) return;
      if (getFieldValue(el) === value) return;
      setFieldValue(el, value, true);
    });
    profileSyncing = false;
  }

  function bindUserProfile(groups, rootSelector = '#configPanelScroll') {
    profileGroups = groups;
    idToProfileGroup = new Map();
    Object.entries(groups).forEach(([groupKey, ids]) => {
      ids.forEach(id => idToProfileGroup.set(id, groupKey));
    });

    const root = document.querySelector(rootSelector);
    if (!root) return;

    const debouncedProfileSavers = new Map();
    Object.keys(groups).forEach(groupKey => {
      const debounced = debounce(() => saveProfileGroupNow(groupKey), DEBOUNCE_MS);
      debouncedProfileSavers.set(groupKey, debounced);
      flushers.push(debounced.flush);
      flushers.push(() => saveProfileGroupNow(groupKey));
    });

    const handleProfileFieldChange = target => {
      if (profileSyncing || !target?.id) return;
      const groupKey = idToProfileGroup.get(target.id);
      if (!groupKey) return;
      const value = getFieldValue(target);
      syncProfileGroupSiblings(target.id, groupKey, value);
      debouncedProfileSavers.get(groupKey)?.();
    };

    root.addEventListener('input', event => handleProfileFieldChange(event.target), true);
    root.addEventListener('change', event => handleProfileFieldChange(event.target), true);
    root.addEventListener('focusout', event => {
      const groupKey = idToProfileGroup.get(event.target?.id);
      if (groupKey) saveProfileGroupNow(groupKey);
    }, true);
  }

  function migrateUserProfile(groups) {
    const profile = {};
    let hasAny = false;
    Object.entries(groups).forEach(([groupKey, ids]) => {
      for (const id of ids) {
        const el = document.getElementById(id);
        if (!el || !isStorable(el)) continue;
        const value = getFieldValue(el);
        if (value !== undefined && String(value).trim() !== '') {
          profile[groupKey] = value;
          hasAny = true;
          break;
        }
      }
    });
    if (hasAny) write(USER_PROFILE_KEY, profile);
    return hasAny;
  }

  function restoreUserProfile(groups, options = {}) {
    const fillEmptyOnly = options.fillEmptyOnly !== false;
    profileGroups = groups;
    let saved = read(USER_PROFILE_KEY);
    if (!saved) {
      migrateUserProfile(groups);
      saved = read(USER_PROFILE_KEY);
    }
    if (!saved) return false;

    let applied = false;
    Object.entries(groups).forEach(([groupKey, ids]) => {
      const value = saved[groupKey];
      if (value == null || value === '') return;
      ids.forEach(id => {
        const el = document.getElementById(id);
        if (!el || !isStorable(el)) return;
        if (fillEmptyOnly && String(getFieldValue(el) ?? '').trim() !== '') return;
        setFieldValue(el, value, true);
        applied = true;
      });
    });
    return applied;
  }

  function flushUserProfile() {
    if (!profileGroups) return;
    Object.keys(profileGroups).forEach(saveProfileGroupNow);
  }

  window.addEventListener('pagehide', () => {
    flushUserProfile();
    flushAll();
  });
  window.addEventListener('beforeunload', () => {
    flushUserProfile();
    flushAll();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      flushUserProfile();
      flushAll();
    }
  });

  function hasSavedFieldValue(fieldId, schemas, profileGroups) {
    if (!fieldId) return false;
    if (profileGroups) {
      const profile = read(USER_PROFILE_KEY);
      if (profile) {
        for (const [groupKey, ids] of Object.entries(profileGroups)) {
          if (!ids.includes(fieldId)) continue;
          const value = profile[groupKey];
          if (value != null && String(value).trim() !== '') return true;
        }
      }
    }
    if (schemas) {
      for (const [schemaKey, ids] of Object.entries(schemas)) {
        if (!ids.includes(fieldId)) continue;
        const saved = read(schemaKey);
        if (!saved || !(fieldId in saved)) continue;
        const value = saved[fieldId];
        if (value != null && String(value).trim() !== '') return true;
      }
    }
    return false;
  }

  global.NOOBIUS_FORM_STORAGE = {
    PREFIX,
    USER_PROFILE_KEY,
    read,
    write,
    hasSaved,
    collectByIds,
    applyByIds,
    bindSchemas,
    bindAutoSave,
    bindUserProfile,
    restore,
    restoreUserProfile,
    migrateUserProfile,
    hasSavedFieldValue,
    flushAll,
    flushUserProfile,
    persistAll,
    saveSchemaNow,
  };
})(window);
