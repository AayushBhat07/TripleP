/**
 * Demo accounts, one per role, offered on the sign-in page. The backend seeds them into an empty database
 * (backend/proposalpanda/auth/demo.py); in production only when DEMO_PASSWORD is set.
 */

export const DEMO_EMAILS = [
  'admin@proposalpanda.dev',
  'analyst@proposalpanda.dev',
  'writer@proposalpanda.dev',
  'viewer@proposalpanda.dev',
];
