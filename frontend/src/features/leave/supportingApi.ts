import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type SupportingDocumentOut = Schemas['SupportingDocumentOut']
export type SupportingDocumentDetailOut = Schemas['SupportingDocumentDetailOut']
export type DownloadTokenOut = Schemas['DownloadTokenOut']

export function useSupportingDocuments(requestId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['leave', 'supporting', requestId],
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/leave/requests/{request_id}/supporting-documents', { params: { path: { request_id: requestId as string } } })),
    enabled: enabled && Boolean(requestId),
  })
}

function useInvalidateSupporting() {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: ['leave'] })
    void client.invalidateQueries({ queryKey: ['approvals'] })
  }
}

/** Multipart like document uploads: the browser sets the boundary; the content type header is removed. */
export function useUploadSupportingDocument() {
  const invalidate = useInvalidateSupporting()
  return useApiMutation({
    mutationFn: async ({ requestId, file }: { requestId: string; file: File }) => {
      const form = new FormData()
      form.append('file', file, file.name)
      return unwrap(
        await api.POST('/api/v1/leave/requests/{request_id}/supporting-documents', {
          params: { path: { request_id: requestId } },
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

export function useDeleteSupportingDocument() {
  const invalidate = useInvalidateSupporting()
  return useApiMutation({
    mutationFn: async ({ documentId, reason }: { documentId: string; reason: string }) =>
      unwrap(await api.DELETE('/api/v1/supporting-documents/{document_id}', { params: { path: { document_id: documentId } }, body: { reason } })),
    onSuccess: invalidate,
  })
}

/** A 60-second single-use link is issued (audited with the reason), then opened as a plain download. */
export function useDownloadSupportingDocument(open: (url: string) => void = openDownload) {
  const invalidate = useInvalidateSupporting()
  return useApiMutation({
    mutationFn: async ({ documentId, reason }: { documentId: string; reason?: string }) => {
      const issued = unwrap(
        await api.POST('/api/v1/supporting-documents/{document_id}/download-token', {
          params: { path: { document_id: documentId } },
          body: { reason: reason?.trim() || null },
        }),
      )
      open(issued.url)
      return issued
    },
    onSuccess: invalidate,
    silent: true,
  })
}

export function openDownload(url: string): void {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noopener'
  anchor.download = ''
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
