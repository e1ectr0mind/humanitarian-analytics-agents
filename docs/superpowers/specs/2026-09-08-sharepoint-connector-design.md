# Дизайн: SharePoint-коннектор через Microsoft Graph (ломоть 2б)

**Дата:** 2026-09-08
**Статус:** утверждён (секции 1–3 согласованы в брейншторм-сессии)
**База:** ядро (1) + коннекторы/чистильщик (2а) + реестр/дизайнер (3) — в master

---

## 1. Контекст и решения брейншторма

Ломоть 2б платформы: четвёртый вид подключения `sharepoint` — табличные
файлы (`.xlsx`/`.csv`) из библиотек документов SharePoint-сайтов и личного
OneDrive, тем же циклом «профиль → список → pull», что Kobo/Ona (2а).
Реальный кейс IMC: 5W и партнёрские выгрузки живут в SharePoint.

Ключевые решения:

- **Auth — device-code flow (delegated), реализация своя на httpx.**
  Рассматривались MSAL (чужой token cache, requests+cryptography рядом с
  httpx, тесты вокруг чужой библиотеки) и msgraph-sdk (тяжёлый, async,
  ломает паттерн «raw httpx» из 2а). Выбран свой flow: два REST-вызова +
  refresh, ~100 строк, «токеном» профиля в keyring становится JSON с
  refresh_token — ложится в существующий credentials-слой без новой
  машинерии. Client-credentials (app-only) — не входит.
- **Что разрешит IT — неизвестно; это конфиг, а не предпосылка.**
  `tenant` и `client_id` — поля профиля. Живая проба на реальном тенанте —
  в конце ломтя; её исход («работает» или «заблокировано политикой,
  нужен запрос в IT») — легитимный результат, закрывающий вопрос
  феазибилити, а не критерий провала.
- **Scope: файлы сайтов + личный OneDrive.** В Graph это одна абстракция
  drive — один кодовый путь. SharePoint Lists — не входят.
- **Адресация — оба способа:** профиль закрепляет сайт (или onedrive) и
  опционально папку, pull — по относительному пути из списка ИЛИ по
  вставленной https-ссылке на файл (Graph shares API).
- **Pull — байт-в-байт.** Файл скачивается как есть, без прогона через
  pandas: многолистовые 5W-файлы нельзя калечить пересборкой.
  Гарантия `_haa_row_id` остаётся особенностью ODK-пуллов.

## 2. Цели и границы

**UX-потоки:**

1. **Подключение:** `haa connect sharepoint --name imc-meal
   --site https://tenant.sharepoint.com/sites/MEAL
   --folder "Shared Documents/5W" --tenant <tenant> --client-id <guid>`
   (или `--site onedrive`; `--folder` опционален, по умолчанию корень
   библиотеки). Вместо `--token` — device flow: CLI печатает код и ссылку
   microsoft.com/devicelogin, поллит токен-эндпоинт, по успеху кладёт в
   keyring JSON с refresh_token. В `connections.toml` — только метаданные
   (kind, site, folder, tenant, client_id), никаких секретов.
2. **Список:** существующий `list_remote_forms` / CLI показывает табличные
   файлы под закреплённой папкой (рекурсивно, потолок 200 позиций), пути
   относительные: `2026/june_5w.xlsx`.
3. **Pull:** по относительному пути, item id или https-ссылке; файл
   пишется в `workspace/data/` байт-в-байт. LLM видит только сводку
   (имя, размер), не содержимое — PII-граница 2а без изменений.
4. **Повторные запуски:** access token обновляется по refresh_token молча.
   Microsoft ротирует refresh-токены — после каждого refresh новый токен
   персистится в keyring. Истёк совсем (неактивность/отзыв) → дружелюбное
   «re-run haa connect sharepoint».

**Не входит в 2б:** запись в SharePoint, SharePoint Lists, поиск по всему
тенанту, синк по расписанию, форматы кроме xlsx/csv, app-only flow.

## 3. Архитектура и компоненты

```
src/haa/connectors/
├─ msauth.py        ← НОВОЕ: device-code flow + refresh на httpx
│     start_device_flow(tenant, client_id) → DeviceFlow(user_code, uri,
│                                              interval, device_code, expires_in)
│     poll_for_token(flow)                 → TokenSet(access, refresh, expires_at)
│     refresh(tenant, client_id, refresh_token) → TokenSet   # ротация!
│     TokenSet ↔ JSON-строка (это и есть «токен» профиля в keyring)
├─ sharepoint.py    ← НОВОЕ: SharePointConnector (тот же протокол list/pull)
│     _drive()      : site-URL → /sites/{host}:{path} → site id → default
│                     drive; "onedrive" → /me/drive (кэш на инстансе)
│     list_forms()  : рекурсивный обход folder (children API, потолок 200),
│                     фильтр .xlsx/.csv → [RemoteForm(uid=item_id,
│                     name=относительный путь)]
│     pull(ref, dir): ref = отн.путь | item id | https-ссылка (shares API);
│                     GET …/content → байты → data_dir/<safe_filename>.<ext>
└─ credentials.py   ← ИЗМЕНЕНИЕ: "sharepoint" в VALID_KINDS и make_connector;
                      Connection += опциональные tenant, client_id, folder
                      (None у kobo/ona; пишутся в connections.toml);
                      URL сайта (или строка "onedrive") живёт в существующем
                      поле base_url
```

**Различение ссылки на pull:** `https://…` → shares API; содержит `/` или
оканчивается на `.xlsx`/`.csv` → относительный путь; иначе — item id.
Потолок листинга — 200 **найденных табличных файлов** (обход прекращается
по достижении).

**Scopes:** `Files.Read.All Sites.Read.All offline_access` (delegated).

**Персист ротации токенов — ключевой механизм.** Коннектор получает не
строку-токен, а её плюс колбэк `on_tokens_updated(json_str)`: после каждого
refresh новый TokenSet уходит через колбэк обратно в keyring (в
`make_connector` колбэк замыкается на профиль). Env-fallback
`HAA_TOKEN_<PROFILE>` работает (JSON в переменной), но ротация тогда живёт
только в памяти сессии — приемлемо для CI/проб.

**Авторизация запросов:** `httpx.Client` с `Authorization: Bearer`; перед
запросом проверка `expires_at`, при 401 — один refresh и повтор, второй
401 → существующая дружелюбная ошибка `get_json` («re-run haa connect»).
Ретраи/5xx/404 — переиспользуется `get_json` из base.py как есть.

**CLI:** у `haa connect` для kind `sharepoint` вместо запроса токена —
device flow (печать кода, поллинг с прогрессом); новые аргументы
`--site`, `--tenant`, `--client-id`, `--folder`. `pull`/`connections`
без изменений.

**Sourcetools MCP:** новых инструментов нет — `list_remote_forms`/
`pull_form` работают через тот же протокол; тексты результатов слегка
генерализуются («items» для sharepoint-профиля), pull возвращает LLM
только сводку.

**Телеметрия:** существующее pull-событие + новое `msauth_refresh`
(профиль, успех/неуспех — без токенов). Новых зависимостей нет.

## 4. Обработка ошибок

| Ситуация | Поведение |
|---|---|
| device flow: `authorization_pending` / `slow_down` | поллинг с учётом `interval`; таймаут по `expires_in` → «код истёк, запустите haa connect заново» |
| device flow: `authorization_declined`, AADSTS-ошибки согласия (65001, 7000218, 700016…) | текст Microsoft + подсказка «тенант не разрешает это приложение — согласуйте client_id с IT или используйте другой» |
| refresh: `invalid_grant` (истёк/отозван) | «сессия Microsoft истекла — re-run haa connect sharepoint» |
| 401/403 на Graph-вызове | один молчаливый refresh + повтор; повторный отказ → дружелюбная ошибка |
| сайт/папка/файл не найдены | 404 → «не найдено»; для pull — список ближайших файлов (как «unknown form» у Kobo) |
| pull не-табличного файла | «поддерживаются .xlsx/.csv» + имя запрошенного |
| битая/чужая https-ссылка | «ссылка не разрешилась в файл — проверьте доступ и что это ссылка на файл, не на папку» |
| листинг упёрся в потолок 200 | список обрезан + пометка «показаны первые 200 — уточните --folder» |
| всё прочее | паттерн 2а: дружелюбные строки, не трейсбеки |

## 5. Тестирование

- **Офлайн (масса, мок httpx-транспорта, паттерн kobo/ona-тестов):**
  msauth — полный device flow (pending → success), ротация refresh (новый
  токен уходит в колбэк), invalid_grant → дружелюбно, таймаут кода;
  sharepoint — резолв сайта и onedrive, рекурсивный листинг с фильтром и
  потолком, pull байт-в-байт (идентичность содержимого), pull по ссылке
  (корректное base64url-кодирование shares API), 401→refresh→повтор;
  credentials — round-trip Connection с новыми полями, kobo/ona не
  сломаны; CLI — парсинг новых аргументов, connect без токена.
- **Live (метка `remote`, вручную):** одна проба на реальном тенанте —
  connect (device flow глазами), list, pull одного файла, повторный запуск
  без re-login (персист ротации). Исход дописывается в спеку
  ретроспективно; «заблокировано политикой тенанта» — валидный исход.
- CI без изменений (офлайн + ruff).

## 6. Критерии приёмки

1. `haa connect sharepoint` проводит device flow; в `connections.toml` —
   метаданные без секретов, токены в keyring.
2. `list` показывает табличные файлы закреплённой папки сайта И
   OneDrive-профиля с относительными путями.
3. `pull` по относительному пути кладёт файл в `workspace/data/`
   байт-в-байт; LLM видит только сводку.
4. `pull` по вставленной https-ссылке работает.
5. Второй запуск сессии не требует повторного логина (ротация
   refresh-токена персистится).
6. Офлайн-тесты и ruff зелёные в CI; live-проба выполнена, исход
   задокументирован.
