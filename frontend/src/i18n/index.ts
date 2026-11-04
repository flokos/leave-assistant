import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import el from './el.json'
import en from './en.json'

export const SUPPORTED_LOCALES = ['en', 'el'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, el: { translation: el } },
  lng: (localStorage.getItem('locale') as Locale | null) ?? 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

export function setLocale(locale: Locale): void {
  localStorage.setItem('locale', locale)
  void i18n.changeLanguage(locale)
}

export default i18n
