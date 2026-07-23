(function (global) {
  'use strict';

  function getTd() {
    return global.tempusDominus;
  }

  function getPickerTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function parsePickerValue(value) {
    if (!value) return null;
    const normalized = String(value).trim().replace(' ', 'T');
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatPickerValue(date, withTime) {
    const pad = (n) => String(n).padStart(2, '0');
    const base = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    if (!withTime) return base;
    return `${base} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function upgradeDateWraps() {
    document.querySelectorAll('.date-input-wrap').forEach((wrap) => {
      const id = wrap.id;
      if (!id) return;
      wrap.classList.add('input-group');
      wrap.dataset.tdTargetInput = 'nearest';
      wrap.dataset.tdTargetToggle = 'nearest';

      const input = wrap.querySelector('input');
      if (input) {
        input.classList.add('form-control');
        input.removeAttribute('data-input');
        input.dataset.tdTarget = `#${id}`;
      }

      const toggle = wrap.querySelector('.date-picker-btn');
      if (toggle) {
        toggle.classList.add('input-group-text');
        toggle.removeAttribute('data-toggle');
        toggle.dataset.tdTarget = `#${id}`;
        toggle.dataset.tdToggle = 'datetimepicker';
      }
    });
  }

  function applyBootstrapFormClasses(root = document) {
    root.querySelectorAll('.field-group input:not([type=file]):not([type=checkbox]):not([type=radio])').forEach((el) => {
      el.classList.add('form-control');
    });
    root.querySelectorAll('.field-group textarea').forEach((el) => {
      el.classList.add('form-control');
    });
    root.querySelectorAll('.field-group select').forEach((el) => {
      el.classList.add('form-select');
    });
  }

  function createPicker(wrapSelector, { withTime = false, onChange, persistKey } = {}) {
    const TD = getTd();
    const wrap = document.querySelector(wrapSelector);
    const input = wrap?.querySelector('input');
    if (!wrap || !input || !TD?.TempusDominus) return null;

    const instance = new TD.TempusDominus(wrap, {
      display: {
        theme: getPickerTheme(),
        buttons: {
          today: true,
          clear: true,
          close: true,
        },
        components: withTime
          ? {
            decades: true,
            year: true,
            month: true,
            date: true,
            hours: true,
            minutes: true,
            seconds: false,
          }
          : {
            decades: true,
            year: true,
            month: true,
            date: true,
            hours: false,
            minutes: false,
            seconds: false,
          },
      },
      localization: {
        format: withTime ? 'yyyy-MM-dd HH:mm' : 'yyyy-MM-dd',
        hourCycle: 'h23',
      },
      allowInputToggle: true,
    });

    const adapter = {
      input,
      instance,
      setDate(value, trigger = true) {
        const parsed = parsePickerValue(value);
        if (!parsed) return;
        instance.dates.setValue(TD.DateTime.convert(parsed));
        if (trigger) {
          input.value = formatPickerValue(parsed, withTime);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        }
      },
      clear() {
        instance.clear();
        input.value = '';
      },
    };

    wrap.addEventListener('change.td', () => {
      const picked = instance.dates.lastPicked;
      if (picked?.toJSDate) {
        input.value = formatPickerValue(picked.toJSDate(), withTime);
      }
      onChange?.();
      if (persistKey) global.__persistFormKey?.(persistKey);
    });

    return adapter;
  }

  global.NOOBIUS_BOOTSTRAP_PICKERS = {
    upgradeDateWraps,
    applyBootstrapFormClasses,
    createPicker,
    parsePickerValue,
    formatPickerValue,
    getPickerTheme,
  };
}(window));
