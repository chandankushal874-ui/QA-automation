import i18n from "i18next";
import Backend from "i18next-http-backend";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

const languages = ["es", "en"] as const;

// List of languages your application supports.
export const supportedLangs = [...languages];

i18n
  // load translation using http -> see /public/locales
  // learn more: https://github.com/i18next/i18next-http-backend
  .use(Backend)
  // detect user language
  // learn more: https://github.com/i18next/i18next-browser-languageDetector
  .use(LanguageDetector)
  // pass the i18n instance to react-i18next.
  .use(initReactI18next)
  // init i18next
  // for all options read: https://www.i18next.com/overview/configuration-options
  .init({
    supportedLngs: languages,
    // map region-specific codes (en-US, es-419, ...) to the base language
    nonExplicitSupportedLngs: true,
    load: "languageOnly",
    fallbackLng: "en",
    debug: false,
  });

export default i18n;
