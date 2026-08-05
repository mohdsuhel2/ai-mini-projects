'use strict';
// Shared by the popup, the content script and Node tests — keep browser-safe.

const IntradayEngine = (() => {

  const SUFFIX = { K: 1e3, M: 1e6, B: 1e9, L: 1e5, CR: 1e7 };

  function parseNum(text) {
    if (text === null || text === undefined) return null;
    let s = String(text).trim()
      .replace(/−/g, '-')      // Unicode minus
      .replace(/[,%\s ]/g, '')
      .replace(/^\+/, '');
    if (!s) return null;
    let mult = 1;
    const m = s.match(/(CR|K|M|B|L)$/i);
    if (m) {
      mult = SUFFIX[m[1].toUpperCase()];
      s = s.slice(0, -m[1].length);
    }
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    return parseFloat(s) * mult;
  }

  return { parseNum };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = IntradayEngine;
if (typeof globalThis !== 'undefined') globalThis.IntradayEngine = IntradayEngine;
