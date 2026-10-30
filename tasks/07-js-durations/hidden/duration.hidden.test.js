'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseDuration, formatDuration } = require('./duration.js');

test('parses every unit, spaces and decimals', () => {
  assert.equal(parseDuration('1h30m'), 5400000);
  assert.equal(parseDuration('250ms'), 250);
  assert.equal(parseDuration('2d 4h'), 187200000);
  assert.equal(parseDuration('1.5h'), 5400000);
  assert.equal(parseDuration('  45s '), 45000);
  assert.equal(parseDuration('1m1ms'), 60001);
  assert.equal(parseDuration('10m 5s 20ms'), 605020);
});

test('a leading minus negates', () => {
  assert.equal(parseDuration('-1m30s'), -90000);
});

test('rejects what is not a duration', () => {
  for (const bad of ['', '   ', 'abc', '5x', '1h junk', 'h', '1h-30m', '--5s']) {
    assert.throws(() => parseDuration(bad), Error, JSON.stringify(bad));
  }
});

test('formats whole units, zero and negatives', () => {
  assert.equal(formatDuration(5400000), '1h30m');
  assert.equal(formatDuration(1500), '1s500ms');
  assert.equal(formatDuration(86400000), '1d');
  assert.equal(formatDuration(0), '0s');
  assert.equal(formatDuration(-90000), '-1m30s');
});

test('round-trips', () => {
  for (const ms of [0, 1, 999, 60001, 90061001, -3723004]) {
    assert.equal(parseDuration(formatDuration(ms)), ms, String(ms));
  }
});
