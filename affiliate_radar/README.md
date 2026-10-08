# MeMarket Affiliate Deal Radar

این رادار هر ۱۵ دقیقه (با فاصله از ابتدای ساعت برای کاهش تأخیرهای GitHub) اجرا می‌شود و فقط آفرهای قوی را به تلگرام می‌فرستد.

## Secrets لازم

در GitHub:
Settings → Secrets and variables → Actions → New repository secret

این ۵ مقدار را بساز:

- MEMARKET_USERNAME — نام کاربری وبمستر MeMarket
- MEMARKET_PASSWORD — رمز وبمستر MeMarket
- MEMARKET_AFFILIATE_CODE — کد همکاری در فروش
- TELEGRAM_BOT_TOKEN — توکن BotFather
- TELEGRAM_CHAT_ID — شناسه کانال/گروه/چت مقصد

مقادیر محرمانه را داخل کد، issue یا commit قرار نده.

## رفتار رادار

- حداقل تخفیف پایدار: ۳۰٪
- آفرهای با تخفیف ۴۰٪+ امتیاز بالاتر می‌گیرند.
- افت قیمت ۸٪+ نسبت به مشاهده قبلی یک سیگنال است.
- موجودی ۵ عدد یا کمتر یک سیگنال کمکی است.
- حداکثر ۳ اعلان در هر اجرا.
- برای هر محصول cooldown شش‌ساعته دارد، مگر اینکه دوباره حداقل ۸٪ از آخرین قیمت اعلان‌شده ارزان‌تر شود.
- وضعیت قیمت در `affiliate_radar/state.json` نگهداری می‌شود.
- درصد تخفیف به معنی مقایسه مستقل با بازار نیست و فقط از قیمت مرجع ثبت‌شده در MeMarket محاسبه می‌شود.

## اجرا

پس از ثبت Secrets، از:
Actions → MeMarket Deal Radar → Run workflow

اولین اجرا را دستی بزن تا اتصال MeMarket و Telegram را تست کنیم.
