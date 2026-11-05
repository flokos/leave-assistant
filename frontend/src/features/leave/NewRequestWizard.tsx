import { Alert, Button, DatePicker, Form, Input, Select, Space, Steps } from 'antd'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { toIso } from '../../shared/dates'
import { type LeaveRequestOut, type SubmitRequestIn, useLeaveTypes, useSubmitRequest, useValidateRequest } from './api'
import { ValidationReportView } from './ValidationReportView'
import { handle } from '../../shared/async'

export interface WizardValues {
  leave_type: string
  dates: [Dayjs, Dayjs]
  reason?: string
}

interface Props {
  onDone?: (request: LeaveRequestOut) => void
  initialValues?: Partial<WizardValues>
}

/** Validate → check → submit: every rule outcome comes from the backend's ValidationReport. */
export function NewRequestWizard({ onDone, initialValues }: Props) {
  const { t, i18n } = useTranslation()
  const [form] = Form.useForm<WizardValues>()
  const [step, setStep] = useState(0)
  const [body, setBody] = useState<SubmitRequestIn | null>(null)
  const types = useLeaveTypes()
  const validate = useValidateRequest()
  const submit = useSubmitRequest()

  const check = async () => {
    const values = await form.validateFields()
    const next: SubmitRequestIn = {
      leave_type: values.leave_type,
      start_date: toIso(values.dates[0]),
      end_date: toIso(values.dates[1]),
      reason: values.reason?.trim() || null,
    }
    setBody(next)
    await validate.mutateAsync(next)
    setStep(1)
  }

  const send = async () => {
    if (!body) return
    const created = await submit.mutateAsync(body)
    setStep(2)
    onDone?.(created)
  }

  return (
    <div data-testid="request-wizard">
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Steps current={step} items={[{ title: t('leave.stepForm') }, { title: t('leave.stepReview') }, { title: t('leave.stepResult') }]} />
      {step === 0 && (
        <Form form={form} layout="vertical" initialValues={{ leave_type: 'annual', ...initialValues }}>
          <Form.Item name="leave_type" label={t('leave.selectType')} rules={[{ required: true }]}>
            <Select
              data-testid="leave-type"
              loading={types.isPending}
              options={(types.data ?? []).map((lt) => ({ value: lt.code, label: i18n.language === 'el' ? lt.name_el : lt.name_en }))}
            />
          </Form.Item>
          <div data-testid="dates">
            <Form.Item name="dates" label={t('leave.selectDates')} rules={[{ required: true }]}>
              <DatePicker.RangePicker format="YYYY-MM-DD" style={{ width: '100%' }} />
            </Form.Item>
          </div>
          <Form.Item name="reason" label={t('common.reason')}>
            <Input.TextArea rows={2} maxLength={1000} placeholder={t('leave.reasonPlaceholder')} data-testid="reason" />
          </Form.Item>
          {validate.error ? <ProblemAlert error={validate.error} /> : null}
          <Button type="primary" onClick={handle(check)} loading={validate.isPending} data-testid="validate">
            {t('leave.validate')}
          </Button>
        </Form>
      )}
      {step === 1 && validate.data && (
        <>
          <ValidationReportView report={validate.data} />
          {submit.error ? <ProblemAlert error={submit.error} /> : null}
          <Space>
            <Button onClick={() => setStep(0)}>{t('common.back')}</Button>
            <Button type="primary" disabled={!validate.data.valid} loading={submit.isPending} onClick={handle(send)} data-testid="submit">
              {t('leave.submit')}
            </Button>
          </Space>
        </>
      )}
      {step === 2 && submit.data && (
        <Alert
          type="success"
          showIcon
          data-testid="submitted"
          message={t('leave.submitted', { number: submit.data.request_number, status: t(`status.${submit.data.status}`) })}
        />
      )}
    </Space>
    </div>
  )
}
