import { Layout, Segmented, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { BrowserRouter, Route, Routes } from 'react-router'
import { HealthPage } from '../features/system/HealthPage'
import { type Locale, setLocale } from '../i18n'
import { AppProviders } from './providers'

function Shell() {
  const { t, i18n } = useTranslation()
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography.Title level={4} style={{ color: 'white', margin: 0 }}>
          {t('app.title')}
        </Typography.Title>
        <Segmented
          aria-label={t('app.language')}
          value={i18n.language}
          options={[
            { label: 'EN', value: 'en' },
            { label: 'EL', value: 'el' },
          ]}
          onChange={(value) => setLocale(value as Locale)}
        />
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<HealthPage />} />
        </Routes>
      </Layout.Content>
    </Layout>
  )
}

export default function App() {
  return (
    <AppProviders>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AppProviders>
  )
}
