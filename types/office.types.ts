/** Office records the API returns: user accounts and audit log entries. */

import type { RoleName } from '@/lib/auth/rbac';

export interface PublicUser {
  id: string;
  email: string;
  name: string;
  role: RoleName;
  disabledAt: string | null;
  createdAt: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  userEmail: string;
  action: string;
  target: string | null;
  detail: string | null;
}
