# نسخهٔ موبایل FamilyGraph

این پوشه پوستهٔ Android را با Capacitor نگه می‌دارد. رابط و داده‌ها از سرور Django می‌آیند؛ بنابراین این نسخه برای کار کامل به آدرس HTTPS سرور نیاز دارد. حالت آفلاین فعلی شامل صفحهٔ آفلاین امن، دارایی‌های ثابت و صف ثبت‌های سریع است، نه اجرای کامل Django یا مدل هوش مصنوعی روی گوشی.

## ساخت APK

پیش‌نیازها: Node.js، Java 17، Android SDK و متغیر `ANDROID_HOME`.

```powershell
cd mobile
npm install
$env:FAMILYGRAPH_SERVER_URL='https://your-domain.example'
npm run configure
npx cap add android
npx cap sync android
cd android
./gradlew.bat assembleDebug
```

فایل خروجی در `mobile/android/app/build/outputs/apk/debug/app-debug.apk` ساخته می‌شود. برای انتشار، باید keystore اختصاصی و build امضاشدهٔ release تنظیم شود.

برای iPhone فایل APK وجود ندارد؛ خروجی iOS باید با Xcode و TestFlight/App Store ساخته و امضا شود.
