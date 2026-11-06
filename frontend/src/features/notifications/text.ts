import type { NotificationOut } from './api'

/** Notification records carry `type` + `params`; the text is an i18n template (e-mails are rendered by the backend, M8). */
export function notificationText(n: NotificationOut, t: (key: string, options?: Record<string, unknown>) => string): string {
  const params = Object.fromEntries(Object.entries(n.params).map(([k, v]) => [k, typeof v === 'string' ? t(`status.${v}`, { defaultValue: t(`leaveTypes.${v}`, { defaultValue: t(`stepRoles.${v}`, { defaultValue: t(`calendar.kinds.${v}`, { defaultValue: t(`calendar.operations.${v}`, { defaultValue: v }) }) }) }) }) : String(v)]))
  return t(`notifications.types.${n.type}`, { ...params, defaultValue: n.type })
}
