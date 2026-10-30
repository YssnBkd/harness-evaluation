'use strict';
// Durations as strings ("1h30m") and as milliseconds.

const UNITS = { d: 86400000, h: 3600000, m: 60000, s: 1000, ms: 1 };

function parseDuration(text) {
  const re = /(\d+)(d|h|m|s|ms)/g;
  let total = 0;
  let match;
  while ((match = re.exec(text))) {
    total += Number(match[1]) * UNITS[match[2]];
  }
  return total;
}

function formatDuration(ms) {
  let out = '';
  for (const [unit, size] of Object.entries(UNITS)) {
    const n = Math.floor(ms / size);
    if (n) {
      out += n + unit;
      ms -= n * size;
    }
  }
  return out;
}

module.exports = { parseDuration, formatDuration };
