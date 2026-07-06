# VendorScreen — Дослідження потреб користувачів та продуктовий roadmap

> Дата: 2026-07-06 · Автор: дослідження на основі кодової бази + аналіз ринку KYC/AML та marketplace monday.com

---

## 1. Що робить додаток сьогодні (як є)

VendorScreen — це **integration-застосунок для monday.com marketplace**, який автоматизує первинний KYC/AML-скринінг вендорів проти санкційних списків і баз PEP через API **OpenSanctions**.

**Модель роботи (recipe / automation block):**
- Тригер monday: *«коли створено новий item»* → POST на `/monday/execute_action`.
- Застосунок читає `boardId`, `itemId`, `statusColumnId`, `detailsColumnId` (їх обирає **клієнт** в UI автоматизації, не з `.env`).
- Бере ім'я item'а як ім'я вендора → `GET /search/default?q=<name>` до OpenSanctions.
- Мапить відповідь на 3 рівні ризику й пише назад у колонки клієнта:
  - **Clear** — збігів немає;
  - **Warning** — PEP або незначні прапорці;
  - **Critical** — прямий збіг з активними санкціями.
- Статус пишеться **за лейблом** (`create_labels_if_missing`), тому працює на будь-якій дошці.

**Цільова аудиторія (з TECH_SPEC):** B2B з високою плинністю підрядників, але без важких ERP — waste management IT, логістика, будівництво, дистрибуція.

**Ключове юридичне позиціонування:** інструмент **лише інформаційний**, рішень не приймає; до кожного результату додається дисклеймер. Відповідальність за комплаєнс — на клієнті.

### Технічний стан (що вже реалізовано в коді)
| Є | Немає |
|---|---|
| Multi-tenant recipe, JWT-верифікація, challenge-handshake | Скринінг лише за **іменем** (endpoint `/search`, без `/match`) |
| Асинхронна обробка + семафор (3 паралельні) | Не використовуються країна / тип сутності / дата народження / ID |
| Retry на 429 від OpenSanctions | Немає повторного (ongoing) скринінгу — лише на створення item'а |
| Health-check, дисклеймер у виводі | Немає історії/audit trail, case-management, дашборду |
| Юридичні документи (ToS, Privacy) | Немає монетизації/білінгу, налаштувань, сповіщень |

---

## 2. Потреби користувачів (аналіз ринку KYC/AML + vendor risk)

Що ринок і практика очікують від інструмента скринінгу вендорів (джерела — sanctions.io, Sanction Scanner, LSEG World-Check, Descartes, Flagright, OpenSanctions docs):

### 2.1. Функціональні очікування (must-have у сегменті)
1. **Точний матчинг, не просто пошук за іменем.** Стандарт — matching engine з fuzzy-порівнянням імені + додаткові атрибути (країна, дата народження, тип сутності, ідентифікатори LEI/ISIN/INN) і **оцінкою впевненості (score)**. OpenSanctions має для цього окремий endpoint `/match` з алгоритмами `logic-v2` / `regression-v2` та поясненнями збігу — зараз він **не використовується**.
2. **PEP + Sanctions + Adverse Media (негативні згадки в ЗМІ).** Це три стандартні шари скринінгу. Зараз є санкції+PEP; adverse media відсутнє.
3. **Ongoing monitoring (постійний перескринінг).** Санкційні списки змінюються — «Clear» сьогодні ≠ «Clear» завтра. Ринок вимагає періодичного re-screening існуючих вендорів і сповіщення при зміні статусу. Зараз — **тільки одноразова перевірка при створенні**.
4. **Audit trail / записи.** Регулятори вимагають зберігати: дату/час скринінгу, використані джерела, результат, рішення. Строк зберігання зазвичай 5–10 років. Зараз історія не зберігається (тільки поточне значення в колонці).
5. **Керування false positives.** Матчинг за іменем дає багато хибних збігів. Потрібні: налаштовуваний поріг score, статус «переглянуто/відхилено», нотатки рецензента.
6. **Case management / workflow ревʼю.** Можливість людині підтвердити або зняти прапорець, призначити відповідального, залишити коментар.
7. **KYB / UBO (для B2B).** Перевірка не лише назви компанії, а й кінцевих бенефіціарів. Довгострокова, але сильно очікувана в B2B.
8. **Bulk / batch скринінг.** Разова перевірка вже наявного списку вендорів (сотні item'ів), не лише нових.

### 2.2. Операційні / UX-очікування
- Робота **там, де вже працює команда** (native-інтеграція в monday — це наша сильна сторона).
- Прозорість методології: чому саме такий вердикт (пояснення score, посилання на профіль — частково вже є).
- Дашборд/звіти: скільки перевірено, скільки Warning/Critical, динаміка.
- Сповіщення (email / monday notification / Slack) при Critical.
- Просте налаштування без коду (частково вже — через recipe UI).

---

## 3. Gap-аналіз: чого бракує для повноцінного продукту

Пріоритезовано за співвідношенням «цінність для користувача / зусилля».

### Категорія A — Якість скринінгу (ядро цінності)
- **A1. Перехід з `/search` на `/match`** з передачею країни/типу сутності та score-порогом. Радикально зменшує хибні збіги.
- **A2. Adverse media** як окремий рівень Warning.
- **A3. Налаштовуваний поріг впевненості** (напр., показувати Warning лише при score ≥ 0.7).

### Категорія B — Життєвий цикл вендора
- **B1. Ongoing monitoring** — плановий перескринінг (щотижня/щомісяця) усіх або лише Warning/Critical вендорів; зміна статусу → сповіщення. Технічно: monday scheduled trigger або власний cron.
- **B2. Bulk-скринінг наявної дошки** — окрема кнопка/дія «перевірити всіх».
- **B3. Re-screen on update** — перевірка при зміні імені item'а.

### Категорія C — Довіра, комплаєнс, аудит
- **C1. Audit log** — окреме сховище (дата, ім'я, запит, сирий результат, вердикт, версія списку). Обов'язково для регульованих клієнтів.
- **C2. Case-workflow** — статуси «на розгляді / підтверджено / хибний збіг» + нотатки рецензента (окрема колонка або item-update).
- **C3. Експорт звіту** (CSV/PDF) для регулятора чи внутрішнього аудиту.

### Категорія D — UX і видимість
- **D1. Дашборд-віджет** (monday board view / dashboard widget) зі зведенням ризиків.
- **D2. Сповіщення при Critical** (monday notification / email / Slack).
- **D3. Пояснення вердикту** з деталізацією score та зіставлених атрибутів.

---

## 4. Що потрібно **тобі** (розробнику) — контроль, підтримка, дохід

Щоб додаток був керованим і прибутковим, а не «поставив і забув».

### 4.1. Монетизація (найважливіше для доходу)
monday marketplace підтримує 3 моделі: **feature-based**, **seat-based**, **account-seat-based**. З вересня 2025 нові seat-based застосунки **зобов'язані** використовувати optimized seat-based (прогресивна ціна за місцем).
- **Рекомендація:** гібрид — **feature-based тарифи** (бо цінність тут не в кількості місць, а в обсязі перевірок і функціях), напр.:
  - **Free / Trial (14 днів):** N перевірок/міс, лише Clear/Warning/Critical за іменем.
  - **Pro:** matching engine + adverse media + ongoing monitoring + audit log, ліміт перевірок вищий.
  - **Business:** необмежені перевірки, bulk, експорт звітів, пріоритетна підтримка.
- Технічно потрібно **інтегрувати monday monetization API** (перевірка активної підписки/плану в кожному запиті `execute_action`), інакше платити ніхто не буде.
- **Контроль витрат:** OpenSanctions — платне API. Треба **лічильник використання на акаунт** і жорсткі ліміти за планом, щоб маржа не з'їдалась.

### 4.2. Спостережуваність та підтримка (operational control)
- **Логування помилок у зовнішній сервіс** (зараз лише stdout) — Sentry/Logtail: бачити падіння в проді.
- **Метрики використання:** к-сть перевірок, розподіл вердиктів, помилки OpenSanctions/monday, латентність. Це і для білінгу, і для здоров'я системи.
- **Health/alerting:** сповіщення собі при сплеску 401/429/5xx.
- **Persistent storage** (зараз stateless) — потрібне для audit log, лічильників, налаштувань, ongoing monitoring. Це фундаментальний архітектурний крок (БД).
- **Graceful degradation:** що писати в колонку, коли OpenSanctions недоступний (окремий статус «Не перевірено / помилка», а не тиша).

### 4.3. Зростання і утримання
- **Onboarding-гайд** усередині monday (перший запуск, підказки з налаштування recipe).
- **Shield Badge** monday (SOC2/ISO або hosting на monday code без винесення даних) — підвищує довіру й конверсію в регульованому сегменті.
- **Сторінка підтримки / зворотний зв'язок**, база знань.
- **Аналітика конверсії trial → paid**.

---

## 5. Фінал: список пріоритетів (roadmap)

Позначки: **Impact** (цінність) / **Effort** (складність). Порядок = рекомендована послідовність.

### 🔴 P0 — Фундамент для доходу і стабільності (робити першим)
| # | Фіча | Impact | Effort | Навіщо |
|---|------|--------|--------|--------|
| 1 | **Монетизація monday API** (перевірка плану + тарифи) | 🔥🔥🔥 | M | Без цього немає доходу взагалі |
| 2 | **Persistent storage (БД)** | 🔥🔥🔥 | M | Розблоковує audit, ліміти, monitoring, налаштування |
| 3 | **Лічильник використання + ліміти за планом** | 🔥🔥🔥 | S | Захист маржі (OpenSanctions платне) |
| 4 | **Error-tracking + метрики (Sentry/Logtail)** | 🔥🔥 | S | Підтримка проду, бачити падіння |
| 5 | **Обробка недоступності OpenSanctions** (статус «помилка», не тиша) | 🔥🔥 | S | Довіра: користувач не має думати, що Clear |

### 🟠 P1 — Якість скринінгу (ключова відмінність від «іграшки»)
| # | Фіча | Impact | Effort | Навіщо |
|---|------|--------|--------|--------|
| 6 | **Перехід на `/match` + score-поріг + країна/тип** | 🔥🔥🔥 | M | Різко менше хибних збігів — головна скарга ринку |
| 7 | **Audit log + експорт (CSV)** | 🔥🔥🔥 | M | Обов'язкова вимога регуляторів |
| 8 | **Сповіщення при Critical** (monday/email) | 🔥🔥 | S | Ніхто не моніторить колонку вручну |

### 🟡 P2 — Життєвий цикл вендора (утримання й upsell)
| # | Фіча | Impact | Effort | Навіщо |
|---|------|--------|--------|--------|
| 9 | **Ongoing monitoring** (плановий перескринінг + алерт при зміні) | 🔥🔥🔥 | L | Санкції змінюються; сильний аргумент для Pro-плану |
| 10 | **Bulk-скринінг наявної дошки** | 🔥🔥 | M | Швидка цінність для нових клієнтів з готовим списком |
| 11 | **Adverse media** як рівень Warning | 🔥🔥 | M | Третій стандартний шар скринінгу |
| 12 | **Case-workflow** (переглянуто/хибний збіг + нотатки) | 🔥🔥 | M | Керування false positives |

### 🟢 P3 — Зрілість продукту (диференціація)
| # | Фіча | Impact | Effort | Навіщо |
|---|------|--------|--------|--------|
| 13 | **Дашборд-віджет** зі зведенням ризиків | 🔥 | M | Видимість цінності керівництву |
| 14 | **KYB / UBO** (бенефіціари) | 🔥🔥 | L | Глибша B2B-цінність, преміум-сегмент |
| 15 | **Shield Badge (SOC2/ISO/GDPR)** | 🔥 | L | Довіра в регульованому сегменті |
| 16 | **Onboarding-гайд + база знань** | 🔥 | S | Конверсія trial → paid, менше саппорту |

---

### Стисло: 3 наступні кроки
1. **Заклади БД + монетизацію + ліміти** — без цього продукт не заробляє й некерований (P0 #1–3).
2. **Підніми якість скринінгу** через `/match` і додай audit log — це перетворює демо на комплаєнс-інструмент (P1 #6–7).
3. **Додай ongoing monitoring** як флагман Pro-плану — головний аргумент, за який платять регулярно (P2 #9).

---

## Джерела
- [Top 10 AML & Sanctions Screening Software 2025 — sanctions.io](https://www.sanctions.io/blog/blog-top-aml-sanctions-software-2025)
- [12 Best KYC Software Providers 2025 — Sanction Scanner](https://www.sanctionscanner.com/blog/12-best-kyc-software-providers-in-2025-1220)
- [World-Check One KYC Screening — LSEG](https://www.lseg.com/en/risk-intelligence/screening-solutions/world-check-kyc-screening/one-kyc-verification)
- [Best Practices for Reducing False Positives in OFAC Screening — Descartes](https://www.descartes.com/resources/knowledge-center/best-practices-and-tools-reducing-false-positives-ofac-sanctions)
- [How to Reduce False Positives in Sanctions Screening — Sardine](https://www.sardine.ai/blog/rules-to-reduce-false-positives-in-sanctions-screening)
- [Understanding Sanctions and Watchlist Screening — Flagright](https://www.flagright.com/post/understanding-sanctions-and-sanctions-screening)
- [The matching API — OpenSanctions](https://www.opensanctions.org/docs/api/matching/)
- [Supported matching algorithms — OpenSanctions](https://www.opensanctions.org/matcher/)
- [Configuring the scoring system — OpenSanctions](https://www.opensanctions.org/docs/api/scoring/)
- [Plans and pricing — monday.com developers](https://developer.monday.com/apps/docs/plans-and-pricing)
- [Updated seat-based pricing for marketplace apps — monday.com](https://developer.monday.com/apps/changelog/updated-seat-based-pricing-for-marketplace-apps)
- [Implement monday.com's monetization](https://developer.monday.com/apps/docs/implementing-monetization)
- [Understanding monday marketplace security](https://support.monday.com/hc/en-us/articles/360017126139-Understanding-monday-marketplace-security)
- [Third-party risk management in 2025 — Diligent](https://www.diligent.com/resources/guides/third-party-risk-management)
