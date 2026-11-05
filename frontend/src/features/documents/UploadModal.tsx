import { InboxOutlined } from '@ant-design/icons'
import { Form, Input, Modal, Select, Typography, Upload } from 'antd'
import type { UploadFile } from 'antd/es/upload/interface'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { usePrincipal } from '../../auth/usePrincipal'
import { handle } from '../../shared/async'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type DocumentCategory, useAddVersion, useUploadDocument } from './api'
import { uploadCategoriesFor } from './permissions'

const ACCEPT = '.pdf,.docx,.txt,.md'

interface Props {
  open: boolean
  onClose: () => void
  /** When set, the modal adds a version to that document instead of creating a new one. */
  documentId?: string | null
}

export function UploadModal({ open, onClose, documentId }: Props) {
  const { t } = useTranslation()
  const principal = usePrincipal()
  const [form] = Form.useForm<{ title: string; category: DocumentCategory }>()
  const [files, setFiles] = useState<UploadFile[]>([])
  const upload = useUploadDocument()
  const addVersion = useAddVersion()
  const categories = uploadCategoriesFor(principal.roles)
  const pending = upload.isPending || addVersion.isPending
  const error = upload.error ?? addVersion.error

  const close = () => {
    form.resetFields()
    setFiles([])
    upload.reset()
    addVersion.reset()
    onClose()
  }

  const submit = async () => {
    const file = files[0]?.originFileObj
    if (!file) return
    if (documentId) {
      await addVersion.mutateAsync({ documentId, file })
    } else {
      const values = await form.validateFields()
      await upload.mutateAsync({
        file,
        title: values.title,
        category: values.category,
        department_id: values.category === 'department_notice' ? principal.me?.employee?.department?.id ?? null : null,
      })
    }
    close()
  }

  return (
    <Modal
      open={open}
      title={documentId ? t('documents.newVersion') : t('documents.upload')}
      onCancel={close}
      onOk={handle(submit)}
      okText={t('documents.upload')}
      okButtonProps={{ disabled: files.length === 0 }}
      confirmLoading={pending}
      destroyOnHidden
    >
      {error ? <ProblemAlert error={error} /> : null}
      <Form form={form} layout="vertical" initialValues={{ category: categories[0] }}>
        {!documentId && (
          <>
            <Form.Item name="title" label={t('documents.docTitle')} rules={[{ required: true, max: 200 }]}>
              <Input data-testid="document-title" />
            </Form.Item>
            <Form.Item name="category" label={t('documents.category')} rules={[{ required: true }]}>
              <Select data-testid="document-category" options={categories.map((c) => ({ value: c, label: t(`documents.categories.${c}`) }))} />
            </Form.Item>
          </>
        )}
        <div data-testid="document-file">
          <Upload.Dragger
            accept={ACCEPT}
            maxCount={1}
            fileList={files}
            beforeUpload={() => false}
            onChange={({ fileList }) => setFiles(fileList.slice(-1))}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">{t('documents.dropHint')}</p>
            <Typography.Text type="secondary">{t('documents.formats')}</Typography.Text>
          </Upload.Dragger>
        </div>
      </Form>
    </Modal>
  )
}
