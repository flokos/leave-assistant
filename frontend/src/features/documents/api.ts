import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type DocumentOut = Schemas['DocumentOut']
export type DocumentDetailOut = Schemas['DocumentDetailOut']
export type VersionOut = Schemas['VersionOut']
export type SearchHitOut = Schemas['SearchHitOut']
export type SearchOut = Schemas['SearchOut']
export type JobOut = Schemas['JobOut']
export type DocumentCategory = DocumentOut['category']

export const CATEGORIES = ['policy_general', 'policy_hr_restricted', 'department_notice', 'admin_internal'] as const
export const VERSION_STATUSES = ['UPLOADED', 'PROCESSING', 'FAILED', 'ACTIVE', 'PENDING_REVIEW', 'QUARANTINED', 'SUPERSEDED', 'RETIRED', 'DELETED'] as const
const IN_FLIGHT = new Set(['UPLOADED', 'PROCESSING'])

export interface DocumentFilters {
  category?: string
  status?: string
  q?: string
}

export function currentVersion(document: DocumentOut): VersionOut | null {
  return document.active_version ?? document.latest_version
}

export function useDocuments(filters: DocumentFilters) {
  return useQuery({
    queryKey: ['documents', 'list', filters],
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/documents', {
          params: { query: { category: filters.category as never, status: filters.status || undefined, q: filters.q || undefined, limit: 200 } },
        }),
      ),
    refetchInterval: (query) => (query.state.data?.items.some((d) => IN_FLIGHT.has(currentVersion(d)?.status ?? '')) ? 3000 : false),
  })
}

export function useDocument(id: string | null) {
  return useQuery({
    queryKey: ['documents', 'detail', id],
    queryFn: async () => unwrap(await api.GET('/api/v1/documents/{document_id}', { params: { path: { document_id: id as string } } })),
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data?.versions.some((v) => IN_FLIGHT.has(v.status)) ? 3000 : false),
  })
}

export function useSearch(q: string) {
  return useQuery({
    queryKey: ['documents', 'search', q],
    queryFn: async () => unwrap(await api.GET('/api/v1/documents/search', { params: { query: { q, k: 10 } } })),
    enabled: q.trim().length >= 2,
  })
}

function useInvalidateDocuments() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['documents'] })
}

export interface UploadInput {
  file: File
  title: string
  category: DocumentCategory
  department_id?: string | null
  language_hint?: string | null
}

/** Multipart: the browser sets the boundary, so the JSON content type is removed and the body passes through. */
export function useUploadDocument() {
  const invalidate = useInvalidateDocuments()
  return useApiMutation({
    mutationFn: async (input: UploadInput) => {
      const form = new FormData()
      form.append('file', input.file, input.file.name)
      form.append('title', input.title)
      form.append('category', input.category)
      if (input.department_id) form.append('department_id', input.department_id)
      if (input.language_hint) form.append('language_hint', input.language_hint)
      return unwrap(
        await api.POST('/api/v1/documents', {
          body: form as never,
          bodySerializer: (body) => body as unknown as FormData,
          headers: { 'Content-Type': null },
        }),
      )
    },
    onSuccess: invalidate,
    silent: true,
  })
}

export function useAddVersion() {
  const invalidate = useInvalidateDocuments()
  return useApiMutation({
    mutationFn: async ({ documentId, file }: { documentId: string; file: File }) => {
      const form = new FormData()
      form.append('file', file, file.name)
      return unwrap(
        await api.POST('/api/v1/documents/{document_id}/versions', {
          params: { path: { document_id: documentId } },
          body: form as never,
          bodySerializer: (body) => body as unknown as FormData,
          headers: { 'Content-Type': null },
        }),
      )
    },
    onSuccess: invalidate,
    silent: true,
  })
}

export function useRetireVersion() {
  const invalidate = useInvalidateDocuments()
  return useApiMutation({
    mutationFn: async ({ documentId, versionNo }: { documentId: string; versionNo: number }) =>
      unwrap(
        await api.POST('/api/v1/documents/{document_id}/versions/{version_no}/retire', {
          params: { path: { document_id: documentId, version_no: versionNo } },
        }),
      ),
    onSuccess: invalidate,
  })
}

export function useReprocessVersion() {
  const invalidate = useInvalidateDocuments()
  return useApiMutation({
    mutationFn: async ({ documentId, versionNo }: { documentId: string; versionNo: number }) =>
      unwrap(
        await api.POST('/api/v1/documents/{document_id}/versions/{version_no}/reprocess', {
          params: { path: { document_id: documentId, version_no: versionNo } },
        }),
      ),
    onSuccess: invalidate,
  })
}

export function useDeleteDocument() {
  const invalidate = useInvalidateDocuments()
  return useApiMutation({
    mutationFn: async ({ documentId, version }: { documentId: string; version: number }) =>
      unwrap(
        await api.DELETE('/api/v1/documents/{document_id}', { params: { path: { document_id: documentId }, query: { version } } }),
      ),
    onSuccess: invalidate,
  })
}

/** Downloads stream through the API with the bearer token (never a storage URL — ADR-0016). */
export async function downloadVersion(documentId: string, versionNo: number, filename: string): Promise<void> {
  const result = await api.GET('/api/v1/documents/{document_id}/versions/{version_no}/download', {
    params: { path: { document_id: documentId, version_no: versionNo } },
    parseAs: 'blob',
  })
  const blob = unwrap(result as { data?: Blob; error?: unknown; response: Response })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
