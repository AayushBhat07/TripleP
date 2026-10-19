import { test } from 'node:test';
import assert from 'node:assert/strict';
import { can, permissionForPath, ROLES } from '../lib/auth/rbac';
import { verifySession } from '../lib/auth/session';

test('only Admin and BidWriter can generate bids', () => {
  assert.deepEqual(
    ROLES.filter(role => can(role, 'bid.generate')),
    ['Admin', 'BidWriter']
  );
});

test('every role can view tenders and bids', () => {
  for (const role of ROLES) {
    assert.ok(can(role, 'tender.view') && can(role, 'bid.view'), role);
  }
});

test('Viewer cannot upload or generate', () => {
  assert.equal(can('Viewer', 'tender.upload'), false);
  assert.equal(can('Viewer', 'bid.generate'), false);
  assert.equal(can('TenderAnalyst', 'bid.generate'), false);
  assert.equal(can(null, 'tender.view'), false);
});

test('routes map to permissions', () => {
  assert.equal(permissionForPath('/api/bid/generate'), 'bid.generate');
  assert.equal(permissionForPath('/api/intelligence/run'), 'tender.upload');
  assert.equal(permissionForPath('/tenders/T-1/bid'), 'bid.view');
  assert.equal(permissionForPath('/tenders/T-1/analysis'), 'tender.view');
  assert.equal(permissionForPath('/dashboard'), null);
});

// Signed by the Python backend (backend/proposalpanda/auth/session.py) with the development secret.
const COOKIE =
  'eyJ1aWQiOiJ1MSIsInZlciI6MSwiZW1haWwiOiJ2aWV3ZXJAcHJvcG9zYWxwYW5kYS5kZXYiLCJuYW1lIjoiViIsInJvbGUiOiJWaWV3ZXIiLCJleHAiOjQxMDI0NDQ4MDB9' +
  '.pJfopRIxrOVXtf_x1VmfJP4O3mjY5Z8USWF0KibGQlA';

test('a session cookie from the backend verifies and tampering is rejected', async () => {
  assert.equal((await verifySession(COOKIE))?.role, 'Viewer');

  const [payload, signature] = COOKIE.split('.');
  const forged = Buffer.from(
    JSON.stringify({ ...JSON.parse(Buffer.from(payload, 'base64url').toString()), role: 'Admin' })
  ).toString('base64url');
  assert.equal(await verifySession(`${forged}.${signature}`), null);
  assert.equal(await verifySession('garbage'), null);
  assert.equal(await verifySession(undefined), null);
});

test('only Admin manages users', () => {
  assert.deepEqual(ROLES.filter(role => can(role, 'user.manage')), ['Admin']);
  assert.equal(permissionForPath('/users'), 'user.manage');
  assert.equal(permissionForPath('/api/users/abc'), 'user.manage');
});
