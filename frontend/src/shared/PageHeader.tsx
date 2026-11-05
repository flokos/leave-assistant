import { Space, Typography } from 'antd'
import type { ReactNode } from 'react'

export function PageHeader({ title, extra, children }: { title: string; extra?: ReactNode; children?: ReactNode }) {
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {extra}
      </div>
      {children}
    </Space>
  )
}
