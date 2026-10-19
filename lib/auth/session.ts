/**
 * Checks the signed session cookie (HMAC-SHA256 via Web Crypto, so it runs in proxy.ts). The backend signs it
 * at login (backend/proposalpanda/auth/session.py); the role lives in it and the browser cannot change it.
 */

import { isRole, type RoleName } from './rbac';

export const SESSION_COOKIE = 'pp_session';

export interface Session {
  /** users.id */
  uid: string;
  /** users.session_version when the cookie was issued; a bump signs this cookie out. */
  ver: number;
  email: string;
  name: string;
  role: RoleName;
  exp: number; // unix seconds
}

const DEV_SECRET = 'proposal-panda-dev-secret-change-me';

function getSecret(): string {
  const secret = process.env.AUTH_SECRET;
  if (secret) return secret;
  if (process.env.NODE_ENV === 'production') {
    throw new Error('AUTH_SECRET must be set in production');
  }
  return DEV_SECRET;
}

const encoder = new TextEncoder();

function fromBase64Url(value: string): Uint8Array {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}

async function hmacKey(): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', encoder.encode(getSecret()), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
}

export async function verifySession(token: string | undefined): Promise<Session | null> {
  if (!token) return null;
  const [payload, signature] = token.split('.');
  if (!payload || !signature) return null;
  try {
    const valid = await crypto.subtle.verify(
      'HMAC',
      await hmacKey(),
      fromBase64Url(signature) as BufferSource,
      encoder.encode(payload)
    );
    if (!valid) return null;
    const session = JSON.parse(new TextDecoder().decode(fromBase64Url(payload))) as Session;
    if (!isRole(session.role) || typeof session.exp !== 'number') return null;
    if (typeof session.uid !== 'string' || typeof session.ver !== 'number') return null;
    if (session.exp < Math.floor(Date.now() / 1000)) return null;
    return session;
  } catch {
    return null;
  }
}
