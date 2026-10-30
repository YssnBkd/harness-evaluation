'use strict';
// Durations as strings ("1h30m") and as milliseconds.

const UNITS = { d: 86400000, h: 3600000, m: 60000, s: 1000, ms: 1 };
const PART = /(\d+(?:\.\d+)?)\s*(ms|d|h|m|s)\s*/y;

function parseDuration(text) {
  if (typeof text !== 'string') throw new TypeError('duration must be a string');
  let s = text.trim();
  const sign = s.startsWith('-') ? -1 : 1;
  if (sign < 0) s = s.slice(1).trimStart();
  if (!s) throw new Error(`not a duration: ${JSON.stringify(text)}`);
  PART.lastIndex = 0;
  let total = 0;
  while (PART.lastIndex < s.length) {
    const match = PART.exec(s);
    if (!match) throw new Error(`not a duration: ${JSON.stringify(text)}`);
    total += Number(match[1]) * UNITS[match[2]];
  }
  return sign * total;
}

function formatDuration(ms) {
  if (ms === 0) return '0s';
  let rest = Math.abs(ms);
  let out = ms < 0 ? '-' : '';
  for (const [unit, size] of Object.entries(UNITS)) {
    const n = Math.floor(rest / size);
    if (n) {
      out += n + unit;
      rest -= n * size;
    }
  }
  return out;
}

module.exports = { parseDuration, formatDuration };
