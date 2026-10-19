import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createVault, memoryBackend, unlockVault, vaultSession, type Backend } from '../lib/vault/vault';
import { decodeRecord, encodeRecord, isVaultKey } from '../lib/vault/wire';
import { moveLocalVault } from '../lib/vault/serverBackend';

test('bytes survive the trip to the server', () => {
  const value = { salt: new Uint8Array([0, 1, 255]), nested: { data: new Uint8Array(70000).fill(7) }, n: 3 };
  const back = decodeRecord<typeof value>(encodeRecord(value));
  assert.deepEqual(back.salt, value.salt);
  assert.deepEqual(back.nested.data, value.nested.data);
  assert.equal(back.n, 3);
});

test('only vault slot names are accepted', () => {
  for (const key of ['header', 'identifiers', 'log', 'doc:3f2a-b', 'file:abc_1']) assert.ok(isVaultKey(key), key);
  for (const key of ['../x', 'doc:', 'users', 'file:a/b']) assert.equal(isVaultKey(key), false, key);
});

test('a browser vault moves to the office once, header last', async () => {
  const local = memoryBackend();
  const key = await createVault(local, 'correct horse battery staple');
  await vaultSession(local, key, 'w').saveIdentifiers({ gstin: 'G', pan: 'P' });
  const office = memoryBackend();
  const order: string[] = [];
  const watched: Backend = { ...office, put: async (k, v) => (order.push(k), office.put(k, v)) };

  assert.equal(await moveLocalVault(local, watched), 'moved');
  assert.equal(order.at(-1), 'header');
  assert.equal((await local.keys()).length, 0);
  const reopened = vaultSession(office, await unlockVault(office, 'correct horse battery staple'), 'w');
  assert.deepEqual(await reopened.identifiers(), { gstin: 'G', pan: 'P' });

  const another = memoryBackend();
  await createVault(another, 'another long passphrase');
  assert.equal(await moveLocalVault(another, office), 'kept');
  assert.ok((await another.keys()).includes('header'), 'kept vault is left alone');
  assert.equal(await moveLocalVault(memoryBackend(), office), 'none');
});
